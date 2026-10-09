from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope


class WebAssets(StaticFiles):
    """Serve browser modules consistently, independent of Windows MIME registry entries."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        content_type = {
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".svg": "image/svg+xml",
            ".json": "application/json; charset=utf-8",
        }.get(path[path.rfind(".") :].lower())
        if content_type:
            response.headers["Content-Type"] = content_type
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
