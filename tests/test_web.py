"""Browser module delivery must not depend on the host's file associations."""

import mimetypes

import httpx

from ainovel.controller.api import create_app
from ainovel.core.config import Settings


async def test_web_modules_have_executable_mime_and_revalidated_cache() -> None:
    mimetypes.init()
    original_type = mimetypes.guess_type("module.js")[0] or "text/javascript"
    mimetypes.add_type("text/plain", ".js")
    try:
        app = create_app(Settings(_env_file=None, llm_provider="mock"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for path in (
                "/assets/app.js",
                "/assets/navigation.js",
                "/assets/client.js",
                "/assets/process-records.js",
                "/assets/characters.js",
                "/assets/discussion.js",
                "/assets/chat-text.js",
            ):
                response = await client.get(path)
                assert response.status_code == 200
                assert response.headers["content-type"] == "text/javascript; charset=utf-8"
                assert response.headers["cache-control"] == "no-cache"
                assert response.headers["x-content-type-options"] == "nosniff"
            for path in ("/", "/character", "/characters", "/discussion", "/assets/bookshelf.css"):
                response = await client.get(path)
                assert response.status_code == 200
                assert response.headers["cache-control"] == "no-cache"
    finally:
        mimetypes.add_type(original_type, ".js")
