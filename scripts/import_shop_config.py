"""One-time private catalog import into a migrated, empty PostgreSQL database."""
from bobaops.core.paths import PRIVATE_DATA_DIR

from sqlalchemy.exc import SQLAlchemyError

from bobaops.catalog.repository import write_config
from bobaops.catalog.schemas import ShopConfig


def main():
    source = PRIVATE_DATA_DIR / 'shop_config.json'
    if not source.exists():
        raise SystemExit('Private shop settings not found. Expected data/private/shop_config.json.')
    try:
        config = ShopConfig.model_validate_json(source.read_text())
        saved = write_config(config, initial_import=True)
    except (ValueError, SQLAlchemyError, KeyError):
        raise SystemExit('Import failed or refused. Verify migrations and an empty database. Existing database edits will not be overwritten. The private JSON file is unchanged.') from None
    print(f'Imported {len(saved.products)} products, {len(saved.stock_items)} stock items, '
          f'{len(saved.preparations)} preparation recipes and {len(saved.questions)} questions.')
    print('Original private JSON is unchanged; PostgreSQL is now the editable source when SHOP_STORAGE=postgres.')


if __name__ == '__main__':
    main()
