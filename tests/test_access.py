import httpx
import pytest
from pydantic import SecretStr

from ainovel.controller.access import (
    COOKIE,
    AccessGate,
    configure_password,
    local_path,
    password_hash,
    verify_password,
)
from ainovel.controller.api import create_app
from ainovel.core.config import Settings

PASSWORD = "test-only-access-password"


@pytest.fixture
def protected_app():
    settings = Settings(
        _env_file=None,
        llm_provider="mock",
        access_password_hash=SecretStr(password_hash(PASSWORD)),
        access_session_secret=SecretStr("test-only-session-signing-secret"),
    )
    app = create_app(settings)

    @app.get("/api/protected-test")
    async def protected():
        return {"private": "novel content"}

    return app


def test_hash_salt_and_env_update_do_not_store_plaintext(tmp_path):
    first, second = password_hash(PASSWORD), password_hash(PASSWORD)
    assert first != second and PASSWORD not in first
    assert verify_password(PASSWORD, first) and not verify_password("incorrect", first)
    assert not verify_password(PASSWORD, "broken")
    path = tmp_path / ".env"
    path.write_text("LLM_API_KEY=preserve-other-secret\nACCESS_PASSWORD_HASH=old\n", encoding="utf-8")
    configure_password(PASSWORD, path)
    text = path.read_text(encoding="utf-8")
    assert "LLM_API_KEY=preserve-other-secret" in text and PASSWORD not in text
    assert text.count("ACCESS_PASSWORD_HASH=") == 1 and "ACCESS_SESSION_SECRET=" in text


@pytest.mark.parametrize("path", ["//evil.test", "https://evil.test", "/\\evil.test", "/\r\nLocation:evil"])
def test_login_redirect_stays_local(path):
    assert local_path(path) == "/"


async def test_whole_site_blocks_pages_static_api_docs_and_streams(protected_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=protected_app), base_url="http://test"
    ) as client:
        for path in ["/", "/docs", "/ui/assets/private.js", "/assets/index.html"]:
            response = await client.get(path)
            assert response.status_code == 303 and response.headers["location"].startswith("/login")
        for path in ["/api/studio/novels", "/health", "/openapi.json", "/api/commands/example/stream"]:
            response = await client.get(path)
            assert response.status_code == 401 and response.json()["login"] == "/login"
        assert (await client.post("/api/studio/novels", json={})).status_code == 401
        login = await client.get("/login")
        assert login.status_code == 200 and "访问密码" in login.text and PASSWORD not in login.text
        assert login.headers["cache-control"] == "no-store"


async def test_correct_password_cookie_and_logout(protected_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=protected_app), base_url="http://test"
    ) as client:
        bad = await client.post("/login", data={"password": "wrong"})
        assert bad.status_code == 401 and COOKIE not in client.cookies
        response = await client.post("/login", data={"password": PASSWORD, "next": "/docs"})
        assert response.status_code == 303 and response.headers["location"] == "/docs"
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie and PASSWORD not in cookie
        assert (await client.get("/api/protected-test")).json() == {"private": "novel content"}
        assert (await client.get("/docs")).status_code == 200
        assert (await client.post("/logout")).status_code == 303
        assert COOKIE not in client.cookies
        assert (await client.get("/api/protected-test")).status_code == 401


async def test_tampering_origin_and_rate_limit(protected_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=protected_app), base_url="http://test"
    ) as client:
        forged = await client.post(
            "/login", data={"password": PASSWORD}, headers={"origin": "http://evil.test"}
        )
        assert forged.status_code == 403
        valid = await client.post("/login", data={"password": PASSWORD, "next": "//evil.test"})
        assert valid.headers["location"] == "/"
        csrf = await client.post("/logout", headers={"origin": "http://evil.test"})
        assert csrf.status_code == 403
        token = client.cookies[COOKIE]
        client.cookies.set(
            COOKIE, token[:-1] + ("0" if token[-1] != "0" else "1"), domain="test.local", path="/"
        )
        assert (await client.get("/api/protected-test")).status_code == 401
        client.cookies.clear()
        for _ in range(8):
            assert (await client.post("/login", data={"password": "wrong"})).status_code == 401
        blocked = await client.post("/login", data={"password": PASSWORD})
        assert blocked.status_code == 429 and blocked.headers["retry-after"] == "60"


def test_changing_password_invalidates_sessions():
    a = Settings(
        _env_file=None,
        access_password_hash=SecretStr(password_hash(PASSWORD)),
        access_session_secret=SecretStr("same-secret"),
    )
    before = AccessGate(a)
    token = before.token()
    assert before.valid(token)
    after = AccessGate(
        a.model_copy(update={"access_password_hash": SecretStr(password_hash("changed-password"))})
    )
    assert not after.valid(token)


async def test_login_tolerates_pasted_outer_whitespace_but_keeps_case(protected_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=protected_app), base_url="http://test"
    ) as client:
        response = await client.post("/login", data={"password": " \r\n" + PASSWORD + "\t "})
        assert response.status_code == 303 and COOKIE in client.cookies
        await client.post("/logout")
        response = await client.post("/login", data={"password": PASSWORD.upper()})
        assert response.status_code == 401 and COOKIE not in client.cookies
        page = (await client.get("/login")).text
        assert "显示密码" in page and 'autocapitalize="none"' in page
