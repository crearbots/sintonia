import secrets
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

TOKEN_NAME = "csrf_token"


def ensure_csrf(request: Request) -> str:
    token = request.session.get(TOKEN_NAME)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[TOKEN_NAME] = token
    return token


def verify_csrf(request: Request, token: str | None) -> bool:
    expected = request.session.get(TOKEN_NAME)
    if not expected or not token:
        return False
    return secrets.compare_digest(expected, token)


def extract_token(content_type: str, body: bytes) -> str | None:
    if not body:
        return None
    if "multipart/form-data" in content_type:
        text = body.decode("utf-8", errors="ignore")
        for marker in ('name="csrf_token"', "name='csrf_token'"):
            i = text.find(marker)
            if i < 0:
                continue
            parts = text[i:].split("\r\n\r\n", 1)
            if len(parts) < 2:
                continue
            value = parts[1].split("\r\n", 1)[0].strip()
            if value:
                return value
        return None
    parsed = parse_qs(body.decode("utf-8", errors="ignore"), keep_blank_values=True)
    vals = parsed.get(TOKEN_NAME)
    return vals[0] if vals else None


class CSRFMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("GET", "POST", "HEAD"):
            await self.app(scope, receive, send)
            return

        if scope["method"] != "POST":
            request = Request(scope, receive)
            ensure_csrf(request)
            await self.app(scope, receive, send)
            return

        body = b""
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.request":
                body += message.get("body", b"")
                more = message.get("more_body", False)
            else:
                more = False

        request = Request(scope)
        token = extract_token(request.headers.get("content-type", ""), body)
        if not verify_csrf(request, token):
            accept = request.headers.get("accept", "")
            if "application/json" in accept:
                response = JSONResponse(
                    {"ok": False, "error": "Sesión no válida. Recarga la página."},
                    status_code=403,
                )
            else:
                response = HTMLResponse(
                    "<!doctype html><meta charset=utf-8><title>Sesión</title>"
                    "<p>La sesión no es válida o expiró. Recarga la página e inténtalo de nuevo.</p>",
                    status_code=403,
                )
            await response(scope, receive, send)
            return

        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay, send)
