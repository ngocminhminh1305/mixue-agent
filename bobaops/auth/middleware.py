"""Default-deny protection, including future routes and uploaded receipt images."""
import hmac
from urllib.parse import urlencode

from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse

from bobaops.auth import security, store

PUBLIC = {'/login', '/api/auth/login', '/assets/app.css', '/assets/login.js'}


def authenticate(request, settings):
    cookie = request.cookies.get(security.COOKIE, '')
    if len(cookie) > 1024:
        return None
    sid = settings.unsign(cookie, 'session', settings.ttl)
    if not isinstance(sid, str) or len(sid) != 43:
        return None
    return sid if store.get_store().valid(sid, settings) else None


class PrivateAppMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        settings = security.load_settings()
        request.state.auth_settings = settings
        response = None
        if request.url.path not in PUBLIC:
            if not settings.configured:
                if request.url.path.startswith('/api/'):
                    response = JSONResponse({'detail': security.SETUP_MESSAGE}, status_code=503)
                else:
                    response = RedirectResponse('/login', status_code=303)
            else:
                try:
                    sid = await run_in_threadpool(authenticate, request, settings)
                except SQLAlchemyError:
                    response = JSONResponse({'detail': 'Chưa kết nối được đăng nhập. Kiểm tra cơ sở dữ liệu và migrations.'}, status_code=503)
                    sid = None
                if response is None and not sid:
                    if request.url.path.startswith('/api/'):
                        response = JSONResponse({'detail': 'Vui lòng đăng nhập.'}, status_code=401)
                    else:
                        target = security.safe_next(request.url.path + ('?' + request.url.query if request.url.query else ''))
                        response = RedirectResponse('/login?' + urlencode({'next': target}), status_code=303)
                if response is None:
                    request.state.session_id = sid
                    if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                        supplied = request.headers.get('x-csrf-token', '')
                        expected = settings.digest(sid, 'csrf')
                        if not security.same_origin(request, settings) or not hmac.compare_digest(supplied.encode(), expected.encode()):
                            response = JSONResponse({'detail': 'Phiên xác nhận không hợp lệ. Tải lại trang rồi thử lại.'}, status_code=403)
        if response is None:
            response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        return response
