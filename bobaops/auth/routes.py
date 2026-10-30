"""One locally configured account; there is no registration or password API."""
import hmac
import json
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from bobaops.auth import security, store
from bobaops.auth.middleware import authenticate
from bobaops.core.paths import FRONTEND_DIR

router = APIRouter()


def cookie(response, name, value, settings, max_age):
    response.set_cookie(name, value, max_age=max_age, httponly=True,
                        secure=settings.secure, samesite='strict', path='/')


@router.get('/login')
def login_page():
    return FileResponse(FRONTEND_DIR / 'login.html')


@router.get('/assets/login.js')
def login_script():
    return FileResponse(FRONTEND_DIR / 'login.js', media_type='text/javascript')


@router.get('/assets/auth.js')
def auth_script():
    return FileResponse(FRONTEND_DIR / 'auth.js', media_type='text/javascript')


@router.get('/api/auth/login')
def login_challenge(request: Request):
    settings = request.state.auth_settings
    if not settings.configured:
        return JSONResponse({'detail': security.SETUP_MESSAGE}, status_code=503)
    token = settings.sign(secrets.token_urlsafe(32), 'login-csrf')
    response = JSONResponse({'csrf_token': token})
    cookie(response, security.CHALLENGE_COOKIE, token, settings, 600)
    return response


@router.post('/api/auth/login')
async def login(request: Request):
    settings = request.state.auth_settings
    if not settings.configured:
        return JSONResponse({'detail': security.SETUP_MESSAGE}, status_code=503)
    token = request.headers.get('x-csrf-token', '')
    challenge = request.cookies.get(security.CHALLENGE_COOKIE, '')
    if (len(token) > 1024 or not security.same_origin(request, settings)
            or not token or not hmac.compare_digest(token.encode(), challenge.encode())
            or not settings.unsign(token, 'login-csrf', 600)):
        return JSONResponse({'detail': 'Tải lại trang đăng nhập rồi thử lại.'}, status_code=403)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 16384:
            return JSONResponse({'detail': 'Thông tin đăng nhập quá dài.'}, status_code=413)
    try:
        data = json.loads(body)
        username, password = data['username'], data['password']
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 100 or len(password) > 1024:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        return JSONResponse({'detail': 'Nhập tên đăng nhập và mật khẩu.'}, status_code=400)

    def check():
        database = store.get_store()
        client = request.client.host if request.client else 'unknown'
        key = settings.digest(client, 'login-limit')
        retry = database.reserve_attempt(key)
        if retry:
            return JSONResponse({'detail': 'Đã thử quá nhiều lần. Vui lòng thử lại sau 15 phút.'},
                                status_code=429, headers={'Retry-After': str(retry)})
        try:
            password_ok = security.PASSWORDS.verify(password, settings.password_hash)
        except (ValueError, TypeError):
            password_ok = False
        if not password_ok or not hmac.compare_digest(username.encode(), settings.username.encode()):
            return JSONResponse({'detail': 'Tên đăng nhập hoặc mật khẩu không đúng.'}, status_code=401)
        old_sid = authenticate(request, settings)
        if old_sid:
            database.revoke(old_sid)
        sid = secrets.token_urlsafe(32)
        database.create(sid, settings)
        database.clear_attempts(key)
        response = JSONResponse({'next': security.safe_next(data.get('next', '/'))})
        cookie(response, security.COOKIE, settings.sign(sid, 'session'), settings, settings.ttl)
        response.delete_cookie(security.CHALLENGE_COOKIE, path='/', secure=settings.secure, httponly=True, samesite='strict')
        return response

    try:
        return await run_in_threadpool(check)
    except SQLAlchemyError:
        return JSONResponse({'detail': 'Chưa kết nối được đăng nhập. Kiểm tra cơ sở dữ liệu và migrations.'}, status_code=503)


@router.get('/api/auth/session')
def session(request: Request):
    settings = request.state.auth_settings
    return {'username': settings.username, 'csrf_token': settings.digest(request.state.session_id, 'csrf')}


@router.post('/api/auth/logout')
def logout(request: Request):
    try:
        store.get_store().revoke(request.state.session_id)
    except SQLAlchemyError:
        return JSONResponse({'detail': 'Chưa đăng xuất được. Kiểm tra kết nối rồi thử lại.'}, status_code=503)
    response = JSONResponse({'ok': True})
    response.delete_cookie(security.COOKIE, path='/', secure=request.state.auth_settings.secure,
                           httponly=True, samesite='strict')
    return response
