import httpx
import pytest

from esl.web.app import create_app


@pytest.fixture
def dist(tmp_path):
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "assets").mkdir()
    (dist_dir / "index.html").write_text("<!doctype html><title>ESL</title>")
    (dist_dir / "assets" / "index-abc123.js").write_text("console.log('app')")
    (dist_dir / "favicon.svg").write_text("<svg/>")
    # Write a sentinel file outside dist to test traversal protection
    (tmp_path / "secret.txt").write_text("CONFIDENTIAL_DATA_SHOULD_NOT_BE_ACCESSIBLE")
    return dist_dir


async def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def spa(dist):
    async with await _client(create_app(frontend_dist=str(dist))) as c:
        yield c


async def test_root_serves_index_without_caching(spa):
    r = await spa.get("/")
    assert r.status_code == 200 and "<title>ESL</title>" in r.text
    assert r.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("path", ["/auth/callback?code=x&state=y", "/settings/square", "/pair"])
async def test_client_routes_serve_index(spa, path):
    r = await spa.get(path)
    assert r.status_code == 200 and "<title>ESL</title>" in r.text


async def test_hashed_assets_are_immutable(spa):
    r = await spa.get("/assets/index-abc123.js")
    assert r.status_code == 200 and "console.log" in r.text
    assert "immutable" in r.headers["cache-control"]


async def test_top_level_static_file(spa):
    r = await spa.get("/favicon.svg")
    assert r.status_code == 200 and r.text == "<svg/>"


@pytest.mark.parametrize("path", ["/v1/does-not-exist", "/api/label-updates/nope", "/docs/nope"])
async def test_api_namespaces_never_fall_back_to_html(spa, path):
    r = await spa.get(path)
    assert r.status_code == 404
    assert "text/html" not in r.headers.get("content-type", "")


async def test_api_routes_still_win(spa):
    assert (await spa.get("/v1/health")).json() == {"status": "ok"}
    assert (await spa.get("/openapi.json")).headers["content-type"].startswith("application/json")


async def test_no_path_traversal(spa):
    r = await spa.get("/%2e%2e/secret.txt")
    assert "CONFIDENTIAL_DATA_SHOULD_NOT_BE_ACCESSIBLE" not in r.text


async def test_no_dist_means_no_frontend(tmp_path):
    async with await _client(create_app(frontend_dist=str(tmp_path / "missing"))) as c:
        assert (await c.get("/")).status_code == 404
