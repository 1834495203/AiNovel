"""Whole-site password gate, including static files, API, docs and event streams."""

import hashlib
import hmac
import secrets
import time
from collections import deque
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.requests import HTTPConnection

COOKIE = "ainovel_access"
ITERATIONS = 310_000


def password_hash(password: str) -> str:
    if not 8 <= len(password) <= 256:
        raise ValueError("密码长度需要为 8–256 个字符")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or len(password) > 256:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def configure_password(password: str, path: Path = Path(".env")) -> None:
    """Change only access settings; never print or rewrite other secrets."""
    values = {
        "ACCESS_PASSWORD": "",
        "ACCESS_PASSWORD_HASH": password_hash(password),
        "ACCESS_SESSION_SECRET": secrets.token_urlsafe(32),
    }
    original = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    lines = original.splitlines()
    for key, value in values.items():
        lines = [line for line in lines if not line.strip().startswith(key + "=")]
        lines.append(key + "=" + value)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def local_path(value: str) -> str:
    if (
        not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or any(ord(c) < 32 for c in value)
    ):
        return "/"
    return value


class AccessGate:
    def __init__(self, settings):
        plain = settings.access_password.get_secret_value()
        if plain and not 4 <= len(plain) <= 256:
            raise ValueError("ACCESS_PASSWORD 长度需要为 4–256 个字符")
        self.plain_digest = hashlib.sha256(plain.encode()).digest() if plain else None
        self.encoded = settings.access_password_hash.get_secret_value()
        self.enabled = bool(self.plain_digest or self.encoded)
        secret = settings.access_session_secret.get_secret_value() or secrets.token_urlsafe(32)
        identity = b"plain:" + self.plain_digest if self.plain_digest else self.encoded.encode()
        self.key = hmac.digest(secret.encode(), identity, "sha256")
        self.ttl = settings.access_session_hours * 3600
        self.failures: dict[str, deque] = {}

    def check_password(self, password: str) -> bool:
        if len(password) > 256:
            return False
        if self.plain_digest is not None:
            return hmac.compare_digest(hashlib.sha256(password.encode()).digest(), self.plain_digest)
        return verify_password(password, self.encoded)

    def token(self) -> str:
        body = f"{int(time.time()) + self.ttl}.{secrets.token_hex(16)}"
        return body + "." + hmac.new(self.key, body.encode(), "sha256").hexdigest()

    def valid(self, token: str) -> bool:
        try:
            expires, nonce, signature = token.split(".")
            body = expires + "." + nonce
            return (
                len(nonce) == 32
                and time.time() < int(expires) <= time.time() + self.ttl + 1
                and hmac.compare_digest(hmac.new(self.key, body.encode(), "sha256").hexdigest(), signature)
            )
        except (ValueError, TypeError):
            return False

    @staticmethod
    def same_origin(request) -> bool:
        origin = request.headers.get("origin")
        if not origin:
            return True
        parts = urlsplit(origin)
        return (
            parts.netloc.lower() == request.headers.get("host", "").lower()
            and parts.scheme == request.url.scheme
        )

    def page(self, next_path="/", error="", status=200):
        next_path = escape(local_path(next_path), quote=True)
        message = f'<p role="alert" class="error">{escape(error)}</p>' if error else ""
        return HTMLResponse(
            """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>访问密码 · 墨间</title><style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f6f5f1;color:#293d36;font:15px/1.7 'Segoe UI','Microsoft YaHei',sans-serif;padding:20px}main{width:380px;max-width:100%;background:white;padding:32px;border:1px solid #dfe3dc;border-radius:14px;box-shadow:0 12px 45px #19372c0b}small{color:#78817b}h1{font-size:25px;margin:9px 0}p{font-size:13px;color:#78817b}label{display:grid;gap:8px;margin:24px 0 14px}input,button{width:100%;font:inherit;padding:11px;border-radius:7px}input{border:1px solid #dfe3dc}button{border:0;background:#356c59;color:white;cursor:pointer}.error{color:#a23c28;background:#fff0e9;padding:10px;border-radius:7px}
</style></head><body><main><small>墨间 · 角色演绎工作区</small><h1>请输入访问密码</h1><p>登录后可以访问小说、字段模板和创作会话。</p>"""
            + message
            + f'<form method="post" action="/login"><input type="hidden" name="next" value="{next_path}"><label>访问密码<input id="access-password" type="password" name="password" autocomplete="current-password" autocapitalize="none" autocorrect="off" spellcheck="false" maxlength="256" required autofocus></label>'
            + """<label style="display:flex;align-items:center;gap:8px;margin:0 0 14px;font-size:13px"><input id="show-password" type="checkbox" style="width:auto">显示密码</label><p>密码区分大小写，可勾选显示后核对。</p><button type="submit">进入工作区</button></form></main><script>document.getElementById('show-password').addEventListener('change',function(){document.getElementById('access-password').type=this.checked?'text':'password';});</script></body></html>""",
            status_code=status,
            headers={
                "Cache-Control": "no-store",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "same-origin",
            },
        )

    async def login(self, request: Request):
        if not self.enabled:
            return RedirectResponse("/", status_code=303)
        if request.method == "GET":
            return self.page(request.query_params.get("next", "/"))
        if not self.same_origin(request):
            return JSONResponse({"detail": "请求来源不匹配"}, status_code=403)
        peer = request.client.host if request.client else "unknown"
        if len(self.failures) > 1024:
            self.failures.clear()
        attempts = self.failures.setdefault(peer, deque())
        now = time.monotonic()
        while attempts and attempts[0] < now - 60:
            attempts.popleft()
        if len(attempts) >= 8:
            response = self.page(error="尝试次数过多，请一分钟后再试。", status=429)
            response.headers["Retry-After"] = "60"
            return response
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 4096:
                return self.page(error="登录请求过大", status=413)
        try:
            form = parse_qs(raw.decode("utf-8"), max_num_fields=4)
        except (UnicodeError, ValueError):
            return self.page(error="登录请求格式错误", status=400)
        password = form.get("password", [""])[0]
        destination = local_path(form.get("next", ["/"])[0])
        correct = self.check_password(password)
        # Preserve deliberately spaced passwords, while tolerating whitespace from copy/paste.
        if not correct and password.strip() != password:
            correct = self.check_password(password.strip())
        if not correct:
            attempts.append(now)
            return self.page(destination, "密码不正确，请重试。", 401)
        self.failures.pop(peer, None)
        response = RedirectResponse(destination, status_code=303, headers={"Cache-Control": "no-store"})
        response.set_cookie(
            COOKIE,
            self.token(),
            max_age=self.ttl,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            path="/",
        )
        return response

    async def logout(self, request: Request):
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE, path="/")
        response.headers["Cache-Control"] = "no-store"
        return response


class AccessMiddleware:
    def __init__(self, app, gate: AccessGate):
        self.app, self.gate = app, gate

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or not self.gate.enabled:
            return await self.app(scope, receive, send)
        connection = HTTPConnection(scope)
        valid = self.gate.valid(connection.cookies.get(COOKIE, ""))
        if scope["type"] == "http" and scope["path"] == "/login" and scope["method"] in ("GET", "POST"):
            return await self.app(scope, receive, send)
        if not valid:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            path = scope["path"]
            if path.startswith("/api/") or path in ("/health", "/openapi.json"):
                response = JSONResponse(
                    {"detail": "请先输入访问密码", "login": "/login"},
                    status_code=401,
                    headers={"Cache-Control": "no-store"},
                )
            else:
                response = RedirectResponse(
                    "/login?next=" + quote(local_path(path), safe=""),
                    status_code=303,
                    headers={"Cache-Control": "no-store"},
                )
            return await response(scope, receive, send)
        if (
            scope["type"] == "http"
            and scope["method"] not in ("GET", "HEAD", "OPTIONS")
            and not self.gate.same_origin(connection)
        ):
            return await JSONResponse({"detail": "请求来源不匹配"}, status_code=403)(scope, receive, send)

        async def protected_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers = [(k, v) for k, v in headers if k.lower() != b"cache-control"]
                headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        return await self.app(scope, receive, protected_send)
