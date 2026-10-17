from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

# First path segments that belong to the API; never answered with index.html.
RESERVED = {"v1", "api", "docs", "redoc", "openapi.json"}


class _ImmutableAssets(StaticFiles):
    """Vite puts a content hash in every asset name, so they can be cached forever."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def mount_frontend(app: FastAPI, dist: str | Path) -> None:
    """Serve the built SPA. Call after every API router is included."""
    if not dist:
        return
    root = Path(dist).resolve()
    index = root / "index.html"
    if not index.is_file():
        return

    if (root / "assets").is_dir():
        app.mount("/assets", _ImmutableAssets(directory=root / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.split("/", 1)[0] in RESERVED:
            raise HTTPException(status_code=404, detail="Not Found")
        if path:
            candidate = (root / path).resolve()
            if candidate.is_file() and candidate.is_relative_to(root):
                return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
