"""Save/review drafts and approve frozen consumption in one transaction."""
import hashlib
import io
import re
import unicodedata
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from bobaops.core.database import get_engine, get_tables, storage_backend
from bobaops.core.paths import PRIVATE_DATA_DIR

router = APIRouter(prefix='/api/sales')
IMAGE_DIR = PRIVATE_DATA_DIR / 'sales_images'
MAX_BYTES = 10 * 1024 * 1024


class ExtractedItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item_name: str | None = Field(max_length=1000)
    quantity: int | None = Field(ge=0, le=2147483647, strict=True)
    issues: list[str] = Field(max_length=100)


class Extraction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    date: date | None
    items: list[ExtractedItem] = Field(max_length=1000)
    issues: list[str] = Field(max_length=100)


class EditedItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str | None = Field(default=None, max_length=100)
    raw_item_name: str = Field(max_length=1000)
    product_id: str | None = Field(default=None, max_length=100)
    quantity: int | None = Field(default=None, ge=0, le=2147483647, strict=True)


class DraftEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    date: date
    items: list[EditedItem] = Field(max_length=1000)


class MatchDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)


class ApproveDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    preview_token: str = Field(pattern=r'^[a-f0-9]{64}$')
    confirm_complete: bool = Field(default=False, strict=True)
    accept_assumptions: bool = Field(default=False, strict=True)


def normalized_name(value):
    # Display names remain untouched. Keep accents and sizes in this comparison key.
    text = unicodedata.normalize('NFC', value or '').casefold()
    return ' '.join(re.sub(r'\(\s*mt\s*\)', ' ', text).split())


def name_and_size(value):
    text = normalized_name(value)
    # Only recognize a final, standalone M/L label. Do not strip words such as
    # "bigsize" or ingredient/variant text elsewhere in the name.
    match = re.search(r'(?:\(\s*(?:size\s*)?([ml])\s*\)|\s+size\s*([ml])|\s+([ml]))$', text)
    if match is None:
        return text, None
    return text[:match.start()].strip(), next(x for x in match.groups() if x).upper()


class MatchingIndex(defaultdict):
    def similarity(self):
        from bobaops.sales.similarity import cached_matcher
        products = tuple(sorted((p['id'], p['name'], name_and_size(p['name'])[0],
                                 p.get('size') or name_and_size(p['name'])[1] or '') for p in self.products))
        surfaces = tuple(sorted((name, owner) for (name, size), owners in self.items() if size is None for owner in owners))
        return cached_matcher(products, surfaces)


def matching_index(products, aliases):
    products = list(products)
    result = MatchingIndex(set)
    result.products = products
    sizes = {}
    for product in products:
        base, label_size = name_and_size(product['name'])
        size = product.get('size') or label_size
        sizes[product['id']] = size
        result[(base, None)].add(product['id'])
        if size:
            result[(base, size)].add(product['id'])
        if product.get('category') == 'topping' and base.startswith('thêm '):
            # Receipts list add-ons by ingredient name; keep the catalog's
            # explicit “Thêm” label and only add exact comparison aliases.
            topping = base[len('thêm '):].strip()
            result[(topping, None)].add(product['id'])
            if topping == 'trân châu nguyên vị':
                result[('trân châu', None)].add(product['id'])
    for alias in aliases:
        if alias['product_id'] not in sizes:
            continue
        base, label_size = name_and_size(alias['name'])
        size = sizes[alias['product_id']]
        if label_size and label_size != size:
            continue  # An alias must not override the catalog's actual cup size.
        result[(base, None)].add(alias['product_id'])
        if size:
            result[(base, size)].add(alias['product_id'])
    return result


def match_product(name, index, available=None):
    base, size = name_and_size(name)
    candidates = index.get((base, size), set())
    if size is None and len(candidates) > 1:
        medium = index.get((base, 'M'), set())
        large = index.get((base, 'L'), set())
        # This shop's receipts omit the M label for a two-size product.
        # Apply that rule only to one M + one L, not ambiguous aliases or
        # unrelated products sharing a comparison key.
        if len(medium) == len(large) == 1 and medium != large and medium | large == candidates:
            candidates = medium
    if len(candidates) != 1:
        return None
    identifier = next(iter(candidates))
    return identifier if available is None or identifier in available else None


def resolve_product(name, index, available):
    """Exact rules win. Fuzzy scores never override exact ambiguity/unavailability."""
    exact = match_product(name, index, available)
    if exact is not None:
        return {'product_id': exact, 'method': 'exact', 'score': 1.0, 'margin': None, 'suggestions': []}
    base, size = name_and_size(name)
    if index.get((base, None)) or index.get((base, size)):
        return {'product_id': None, 'method': 'unmatched', 'score': None, 'margin': None, 'suggestions': []}
    if size is None and re.search(r'\b(?:size|slze|s1ze)\b', base):
        # An unreadable size must not silently fall back to an unlabelled M.
        return {'product_id': None, 'method': 'unmatched', 'score': None, 'margin': None, 'suggestions': []}
    from bobaops.sales.similarity import approximate
    return approximate(base, size, index, available)


def matcher(conn, t):
    # Include inactive sizes so an inactive default M never becomes a guess of L.
    products = catalog(conn, t, include_inactive=True)
    aliases = conn.execute(select(t['product_aliases'])).mappings()
    return matching_index(products, aliases), {p['id'] for p in products if p['active']}


def row_warnings(rows, allowed):
    products = Counter(r['product_id'] for r in rows if r['product_id'])
    names = Counter(normalized_name(r['raw_item_name']) for r in rows if normalized_name(r['raw_item_name']))
    result = {}
    for row in rows:
        issues = []
        if not row['raw_item_name'].strip():
            issues.append('Enter an item name.')
        if row['quantity'] is None:
            issues.append('Enter the quantity.')
        if row['product_id'] not in allowed:
            issues.append('Choose an available product from the catalog.')
        if ((row['product_id'] and products[row['product_id']] > 1) or
                (normalized_name(row['raw_item_name']) and names[normalized_name(row['raw_item_name'])] > 1)):
            issues.append('Possible photo overlap: compare the source photos and remove duplicate rows. Quantities have not been added together.')
        result[row['id']] = issues
    return result


@contextmanager
def connection():
    if storage_backend() != 'postgres':
        raise HTTPException(503, 'Saved sales require SHOP_STORAGE=postgres and a migrated database.')
    try:
        tables = get_tables()
        if 'sales_reversals' not in tables or 'revision' not in tables['sales_reports'].c or 'source_image_id' not in tables['sales_items'].c:
            raise HTTPException(503, 'Run python3 -m alembic upgrade head, then restart the backend to enable saved sales.')
        # Report locks serialize edits and appends; detail readers hold a shared lock.
        with get_engine().connect().execution_options(isolation_level='READ COMMITTED') as conn, conn.begin():
            yield conn, tables
    except IntegrityError:
        raise HTTPException(409, 'A conflicting photo, report date or inventory deduction already exists. Reload the report before trying again.') from None
    except (SQLAlchemyError, KeyError):
        raise HTTPException(503, 'Sales database is not ready. Start PostgreSQL, run python3 -m alembic upgrade head, then restart the backend.') from None


def catalog(conn, t, include_inactive=False):
    p = t['products']
    query = select(p.c.id, p.c.name, p.c.size, p.c.active, p.c.category).where(~p.c.is_deleted)
    if not include_inactive:
        query = query.where(p.c.active)
    return list(conn.execute(query.order_by(p.c.name)).mappings())


def locked_report(conn, t, identifier):
    report = conn.execute(select(t['sales_reports']).where(t['sales_reports'].c.id == identifier).with_for_update()).mappings().first()
    if report is None:
        raise HTTPException(404, 'Sales report not found.')
    if report['status'] != 'draft':
        raise HTTPException(409, 'Only draft reports can be edited.')
    return report


def details(conn, t, identifier):
    report = conn.execute(select(t['sales_reports']).where(t['sales_reports'].c.id == identifier).with_for_update(read=True)).mappings().first()
    if report is None:
        raise HTTPException(404, 'Sales report not found.')
    # Access revision explicitly so an outdated migration gets an actionable error.
    revision = report['revision']
    rows = list(conn.execute(select(t['sales_items']).where(t['sales_items'].c.report_id == identifier)
                             .order_by(t['sales_items'].c.id)).mappings())
    products = catalog(conn, t)
    approval = report.get('approval_snapshot')
    if approval:
        # Approved names/choices come from the frozen calculation, not today's menu.
        products = list({r['product_id']: {'id': r['product_id'], 'name': r['product_name']}
                         for r in approval['rows']}.values())
    warnings = row_warnings(rows, {p['id'] for p in products})
    matches, available = matcher(conn, t) if report['status'] == 'draft' else (None, None)
    images = []
    for row in conn.execute(select(t['sales_images']).where(t['sales_images'].c.report_id == identifier)
                            .order_by(t['sales_images'].c.created_at, t['sales_images'].c.id)).mappings():
        extraction = row['extraction'] or {}
        images.append({'id': row['id'], 'url': f"/api/sales/images/{row['id']}",
                       'extracted_date': extraction.get('date'), 'issues': extraction.get('issues', [])})
    reversal = conn.execute(select(t['sales_reversals']).where(t['sales_reversals'].c.report_id == identifier)).mappings().first()
    replaces = conn.scalar(select(t['sales_reversals'].c.report_id).where(t['sales_reversals'].c.replacement_report_id == identifier))
    return {'id': report['id'], 'date': report['report_date'], 'status': report['status'], 'revision': revision,
            'reversal': dict(reversal) if reversal else None, 'replaces_report_id': replaces,
            'approved_at': report['approved_at'], 'approval': approval,
            'issues': report['issues'], 'images': images, 'products': [dict(p) for p in products],
            'items': [{'id': r['id'], 'raw_item_name': r['raw_item_name'], 'product_id': r['product_id'],
                       'quantity': r['quantity'], 'source_image_id': r['source_image_id'],
                       'extraction_issues': r['issues'], 'issues': warnings[r['id']],
                       'matching': resolve_product(r['raw_item_name'], matches, available) if matches is not None else None} for r in rows]}


def save_extraction(extraction, data, extension, report_date, powder_usage=None):
    if powder_usage is not None and powder_usage.used_on != report_date:
        raise HTTPException(422, 'Ngày dùng bột phải trùng ngày trên hoá đơn.')
    digest = hashlib.sha256(data).hexdigest()
    written = None
    try:
        with connection() as (conn, t):
            # Serialize uploads of the same photo across dates, including concurrent requests.
            conn.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': int.from_bytes(bytes.fromhex(digest)[:8], 'big', signed=True)})
            existing = conn.execute(select(t['sales_images'].c.report_id)
                .join(t['sales_reports'], t['sales_images'].c.report_id == t['sales_reports'].c.id)
                .where(t['sales_images'].c.content_sha256 == digest, t['sales_reports'].c.status != 'reversed')).first()
            if existing:
                raise HTTPException(409, 'This exact photo is already saved. Open Saved sales to review it; it has not been added again.')
            reports = t['sales_reports']
            conn.execute(pg_insert(reports).values(id=uuid4().hex, report_date=report_date)
                         .on_conflict_do_nothing(index_elements=[reports.c.report_date], index_where=reports.c.status != 'reversed'))
            identifier = conn.scalar(select(reports.c.id).where(reports.c.report_date == report_date, reports.c.status != 'reversed'))
            report = locked_report(conn, t, identifier)
            products = catalog(conn, t)
            if not products:
                raise HTTPException(400, 'Add or import your products in Shop setup before saving sales.')
            matches, available = matcher(conn, t)
            image_id = uuid4().hex
            IMAGE_DIR.mkdir(parents=True, exist_ok=True)
            written = IMAGE_DIR / f'{image_id}.{extension}'
            with written.open('xb') as file:
                file.write(data)
            conn.execute(insert(t['sales_images']).values(id=image_id, report_id=identifier,
                         content_sha256=digest, source_path=written.name, extraction=extraction.model_dump(mode='json')))
            for index, item in enumerate(extraction.items):
                product_id = resolve_product(item.item_name, matches, available)['product_id']
                conn.execute(insert(t['sales_items']).values(id=f'{image_id}_{index:04d}', report_id=identifier,
                             source_image_id=image_id, raw_item_name=item.item_name or '', product_id=product_id,
                             quantity=item.quantity, issues=item.issues))
            if powder_usage is not None:
                from bobaops.inventory.powder_usage import apply_daily_usage
                from bobaops.inventory.routes import execute_in_transaction
                execute_in_transaction(conn, t, 'ice_cream_usage', powder_usage,
                                       lambda c, tables, operation_id: apply_daily_usage(c, tables, operation_id, powder_usage))
            conn.execute(update(reports).where(reports.c.id == identifier).values(revision=report['revision'] + 1))
            result = details(conn, t, identifier)
        return result
    except BaseException:
        if written is not None:
            written.unlink(missing_ok=True)
        raise


@router.post('/drafts', status_code=201)
async def create_draft(report: str = Form(...), report_date: date = Form(...), image: UploadFile = File(...),
                       powder_usage: str | None = Form(default=None)):
    try:
        extraction = Extraction.model_validate_json(report)
        if powder_usage is not None:
            from bobaops.inventory.powder_usage import UsageRequest
            powder_usage = UsageRequest.model_validate_json(powder_usage)
    except ValidationError:
        raise HTTPException(422, 'Check the report date, item names and whole-number quantities before saving.') from None
    try:
        data = await image.read(MAX_BYTES + 1)
    finally:
        await image.close()
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(400, 'Choose an image smaller than 10 MB.')
    try:
        with Image.open(io.BytesIO(data)) as photo:
            extension = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}.get(photo.format)
            if extension is None:
                raise HTTPException(400, 'Supported images: JPG, PNG, and WebP.')
            photo.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise HTTPException(400, 'This file is not a readable image.') from None
    # File writes and synchronous SQL run off the async request event loop.
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(save_extraction, extraction, data, extension, report_date, powder_usage)


@router.get('/reports')
def list_reports():
    with connection() as (conn, t):
        reports, items, images = t['sales_reports'], t['sales_items'], t['sales_images']
        row_count = select(func.count()).where(items.c.report_id == reports.c.id).correlate(reports).scalar_subquery()
        photo_count = select(func.count()).where(images.c.report_id == reports.c.id).correlate(reports).scalar_subquery()
        return [dict(r) for r in conn.execute(select(reports.c.id, reports.c.report_date.label('date'), reports.c.status,
                reports.c.revision, row_count.label('item_count'), photo_count.label('image_count'))
                .order_by(reports.c.report_date.desc(), reports.c.status == 'reversed', reports.c.created_at.desc(), reports.c.id).limit(100)).mappings()]


@router.get('/reports/{identifier}')
def get_report(identifier: str):
    with connection() as (conn, t):
        return details(conn, t, identifier)


@router.post('/reports/{identifier}/match')
def match_draft(identifier: str, edit: MatchDraft):
    with connection() as (conn, t):
        report = locked_report(conn, t, identifier)
        if report['revision'] != edit.revision:
            raise HTTPException(409, 'This draft changed. Reload it before matching products.')
        index, available = matcher(conn, t)
        rows = conn.execute(select(t['sales_items']).where(t['sales_items'].c.report_id == identifier,
                            t['sales_items'].c.product_id.is_(None))).mappings().all()
        count = 0
        for row in rows:
            product_id = resolve_product(row['raw_item_name'], index, available)['product_id']
            if product_id is not None:
                conn.execute(update(t['sales_items']).where(t['sales_items'].c.id == row['id']).values(product_id=product_id))
                count += 1
        if count:
            conn.execute(update(t['sales_reports']).where(t['sales_reports'].c.id == identifier)
                         .values(revision=report['revision'] + 1))
        result = details(conn, t, identifier)
        result['matched_count'] = count
        return result


@router.put('/reports/{identifier}')
def edit_report(identifier: str, edit: DraftEdit):
    with connection() as (conn, t):
        report = locked_report(conn, t, identifier)
        if report['revision'] != edit.revision:
            raise HTTPException(409, 'This draft changed in another tab or received a new photo. Reload it before saving.')
        existing = {r['id']: r for r in conn.execute(select(t['sales_items']).where(t['sales_items'].c.report_id == identifier)).mappings()}
        ids = [r.id for r in edit.items if r.id is not None]
        if len(ids) != len(set(ids)) or any(i not in existing for i in ids):
            raise HTTPException(400, 'Invalid or repeated sales row ID. Reload the report.')
        allowed = {p['id'] for p in catalog(conn, t)}
        for item in edit.items:
            if item.product_id is not None and item.product_id not in allowed:
                raise HTTPException(400, 'Choose an available product or leave the row unmatched.')
        removed = set(existing) - set(ids)
        if removed:
            conn.execute(delete(t['sales_items']).where(t['sales_items'].c.id.in_(removed)))
        for item in edit.items:
            values = item.model_dump(exclude={'id'})
            if item.id is None:
                conn.execute(insert(t['sales_items']).values(id=uuid4().hex, report_id=identifier, **values))
            else:
                conn.execute(update(t['sales_items']).where(t['sales_items'].c.id == item.id).values(**values))
        conn.execute(update(t['sales_reports']).where(t['sales_reports'].c.id == identifier)
                     .values(report_date=edit.date, revision=report['revision'] + 1))
        return details(conn, t, identifier)


def require_approval_schema(t):
    if 'approval_snapshot' not in t['sales_reports'].c:
        raise HTTPException(503, 'Run python3 -m alembic upgrade head, then restart the backend to enable consumption approval.')


@router.get('/reports/{identifier}/consumption')
def preview_consumption(identifier: str):
    from bobaops.inventory.calculations import build_preview
    with connection() as (conn, t):
        require_approval_schema(t)
        report = conn.execute(select(t['sales_reports']).where(t['sales_reports'].c.id == identifier)
                              .with_for_update(read=True)).mappings().first()
        if report is None:
            raise HTTPException(404, 'Sales report not found.')
        if report['status'] != 'draft':
            if report['approval_snapshot']:
                return report['approval_snapshot']
            raise HTTPException(409, 'This older approved report has no saved consumption snapshot. It will not be recalculated.')
        revision = conn.scalar(select(t['shop_state'].c.revision).where(t['shop_state'].c.id == 1).with_for_update(read=True))
        return build_preview(conn, t, report, revision)


@router.post('/reports/{identifier}/approve')
def approve_report(identifier: str, approval: ApproveDraft):
    from bobaops.inventory.calculations import build_preview
    with connection() as (conn, t):
        require_approval_schema(t)
        report = locked_report(conn, t, identifier)
        if report['revision'] != approval.revision:
            raise HTTPException(409, 'The report changed. Reload it and preview consumption again.')
        revision = conn.scalar(select(t['shop_state'].c.revision).where(t['shop_state'].c.id == 1).with_for_update(read=True))
        # Serialize inventory writers before reading balances. Catalog writers take
        # shop_state first too, so edits cannot change a recipe during this approval.
        conn.execute(select(t['stock_items'].c.id).order_by(t['stock_items'].c.id).with_for_update()).all()
        plan = build_preview(conn, t, report, revision)
        if plan['preview_token'] != approval.preview_token:
            raise HTTPException(409, 'Recipes, sales or stock changed since the preview. Preview consumption again before approving.')
        if plan['blockers']:
            raise HTTPException(400, 'Resolve the blocking issues in the consumption preview before approving.')
        if not approval.confirm_complete:
            raise HTTPException(400, 'Confirm that all photos and rows were reviewed and the daily report is complete.')
        if plan['assumptions'] and not approval.accept_assumptions:
            raise HTTPException(400, 'Acknowledge the provisional recipes/yields/prices shown in the preview.')
        row_ids = [r['sale_item_id'] for r in plan['rows']]
        if conn.scalar(select(t['inventory_movements'].c.id).where(t['inventory_movements'].c.sale_item_id.in_(row_ids)).limit(1)):
            raise HTTPException(409, 'Inventory movements already exist for this report. It cannot deduct stock again.')
        approved_at = datetime.now(timezone.utc)
        for row in plan['rows']:
            conn.execute(update(t['sales_items']).where(t['sales_items'].c.id == row['sale_item_id'])
                         .values(recipe_id=row['recipe_id'], price_version_id=row['price_version_id'], unit_price_vnd=row['unit_price_vnd']))
            conn.execute(delete(t['sales_preparation_versions']).where(t['sales_preparation_versions'].c.sales_item_id == row['sale_item_id']))
            for prep_id in row['preparation_version_ids']:
                conn.execute(insert(t['sales_preparation_versions']).values(sales_item_id=row['sale_item_id'], preparation_version_id=prep_id))
            for deduction in row['deductions']:
                conn.execute(insert(t['inventory_movements']).values(id=uuid4().hex, item_id=deduction['item_id'],
                             kind='sale', signed_quantity=-Decimal(deduction['quantity']), occurred_at=approved_at,
                             sale_item_id=row['sale_item_id'], note=f'Daily sales {report["report_date"]}'))
        plan.update(approved=True, can_approve=False, approved_at=approved_at.isoformat(),
                    confirmed_complete=True, accepted_assumptions=approval.accept_assumptions,
                    reviewed_preview_token=plan['preview_token'], preview_token=None,
                    revision=report['revision'] + 1)
        conn.execute(update(t['sales_reports']).where(t['sales_reports'].c.id == identifier)
                     .values(status='approved', approved_at=approved_at, revision=report['revision'] + 1, approval_snapshot=plan))
        return {'report': details(conn, t, identifier), 'preview': plan}


@router.get('/images/{identifier}')
def get_image(identifier: str):
    with connection() as (conn, t):
        name = conn.scalar(select(t['sales_images'].c.source_path).where(t['sales_images'].c.id == identifier))
    if not name:
        raise HTTPException(404, 'Photo not found.')
    path = (IMAGE_DIR / name).resolve()
    if path.parent != IMAGE_DIR.resolve() or not path.is_file():
        raise HTTPException(404, 'Photo file not found. Include private sales_images in your backups.')
    return FileResponse(path, headers={'Cache-Control': 'no-store'})


@router.delete('/reports/{identifier}')
def delete_draft(identifier: str, revision: int = Query(ge=0)):
    with connection() as (conn, t):
        report = locked_report(conn, t, identifier)
        if report['revision'] != revision:
            raise HTTPException(409, 'This draft changed. Reload it before deleting.')
        row_ids = select(t['sales_items'].c.id).where(t['sales_items'].c.report_id == identifier)
        movement = conn.scalar(select(t['inventory_movements'].c.id)
                               .where(t['inventory_movements'].c.sale_item_id.in_(row_ids)).limit(1))
        if movement is not None:
            raise HTTPException(409, 'This report has inventory movements and cannot be deleted as a draft.')
        filenames = list(conn.scalars(select(t['sales_images'].c.source_path)
                                     .where(t['sales_images'].c.report_id == identifier)))
        conn.execute(delete(t['sales_preparation_versions']).where(t['sales_preparation_versions'].c.sales_item_id.in_(row_ids)))
        conn.execute(delete(t['sales_items']).where(t['sales_items'].c.report_id == identifier))
        conn.execute(delete(t['sales_images']).where(t['sales_images'].c.report_id == identifier))
        conn.execute(delete(t['sales_reports']).where(t['sales_reports'].c.id == identifier))
        # Correction drafts share immutable files with their retained original report.
        referenced = set(conn.scalars(select(t['sales_images'].c.source_path).where(t['sales_images'].c.source_path.in_(filenames))))
        filenames = [name for name in filenames if name not in referenced]
        report_date = report['report_date']
    # Delete files only after the database commit. A file cleanup failure must not
    # misreport a successful deletion or prevent the image from being uploaded again.
    cleanup_failed = False
    for name in filenames:
        try:
            path = (IMAGE_DIR / name).resolve()
            if path.parent != IMAGE_DIR.resolve():
                cleanup_failed = True
                continue
            path.unlink(missing_ok=True)
        except OSError:
            cleanup_failed = True
    return {'deleted': True, 'date': report_date, 'photo_cleanup_pending': cleanup_failed}
