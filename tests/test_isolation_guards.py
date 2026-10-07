from fastapi.routing import APIRoute
from sqlalchemy import text

import esl.db
from esl.gateway.auth import get_gateway_session
from esl.web.app import app
from esl.web.deps import get_business_session, get_current_user

# Routes that touch no business data, or resolve the business themselves:
# - the Square webhook resolves it from the signed event's merchant_id;
# - the OAuth callback resolves it from the single-use state row
#   (esl_oauth_state_take). Square redirects the browser here without our token.
UNSCOPED_ROUTES = {"/v1/health", "/v1/webhooks/square", "/v1/square/oauth/callback", "/{path:path}"}

# The store Pi's routes: no dashboard user, authenticated by X-Api-Key (get_gateway_session).
GATEWAY_ROUTES = {"/api/label-updates/pending", "/api/label-updates/status", "/api/labels/telemetry"}

# Known scoped routes that must exist and be checked.
REQUIRED_SCOPED_ROUTES = {
    "/v1/tags", "/v1/tags/{tag_id}", "/v1/catalog/variations",
    "/v1/me", "/v1/stores", "/v1/staff", "/v1/staff/{user_id}",
    "/v1/square/connection", "/v1/square/oauth/start", "/v1/square/locations", "/v1/stores/selection",
    "/v1/label-updates/{update_id}", "/v1/stores/{store_id}/labels", "/v1/stores/{store_id}/gateways",
    "/v1/scan/classify", "/v1/catalog/lookup",
}


async def test_every_business_table_has_forced_rls(admin_session):
    rows = (await admin_session.execute(text("""
        select c.table_name,
               cl.relrowsecurity and cl.relforcerowsecurity as forced,
               exists (select 1 from pg_policies p
                       where p.schemaname = 'public' and p.tablename = c.table_name
                         and p.policyname = 'business_isolation') as has_policy
        from information_schema.columns c
        join pg_class cl on cl.relname = c.table_name and cl.relkind = 'r'
        join pg_namespace n on n.oid = cl.relnamespace and n.nspname = 'public'
        where c.table_schema = 'public' and c.column_name = 'business_id'
        union all
        select 'businesses', relrowsecurity and relforcerowsecurity,
               exists (select 1 from pg_policies where tablename = 'businesses'
                         and policyname = 'business_isolation')
        from pg_class where relname = 'businesses'
    """))).all()
    assert len(rows) >= 6
    unprotected = [r.table_name for r in rows if not (r.forced and r.has_policy)]
    assert unprotected == []


def _dependency_calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _dependency_calls(dep)


def test_every_route_uses_business_session():
    # FastAPI 0.141.1 wraps router includes in _IncludedRouter, which is not
    # an APIRoute. We can't simply filter app.routes on isinstance(APIRoute).
    # Instead, use effective_route_contexts() to flatten nested includes and
    # get the full path and dependant for each route.
    routes_with_paths = []

    for route in app.routes:
        if isinstance(route, APIRoute):
            # Starlette/OpenAPI doc routes (not router-included API routes)
            routes_with_paths.append((route.path, route))
        elif hasattr(route, 'effective_route_contexts'):
            # _IncludedRouter: flatten nested includes
            for context in route.effective_route_contexts():
                full_path = context.path
                routes_with_paths.append((full_path, context))
        elif hasattr(route, 'path') and any(route.path.startswith(p) for p in ['/openapi', '/docs', '/redoc']):
            # Built-in Starlette routes for OpenAPI/docs (skip, not part of business API)
            pass
        else:
            # Unknown route type; fail hard
            assert False, f"Unexpected route type {type(route).__name__}: {route}"

    # Floor assertion: ensure we found the known routes (prevents vacuous pass)
    collected_paths = {path for path, _ in routes_with_paths}
    assert REQUIRED_SCOPED_ROUTES <= collected_paths, (
        f"Missing required routes. Found: {collected_paths}, "
        f"Expected at least: {REQUIRED_SCOPED_ROUTES}"
    )

    unscoped = [
        full_path
        for full_path, route_or_context in routes_with_paths
        if full_path not in UNSCOPED_ROUTES and not full_path.startswith("/api/")
        and get_business_session not in set(_dependency_calls(route_or_context.dependant))
    ]
    assert unscoped == []

    anonymous = [
        full_path
        for full_path, route_or_context in routes_with_paths
        if full_path not in UNSCOPED_ROUTES and not full_path.startswith("/api/")
        and get_current_user not in set(_dependency_calls(route_or_context.dependant))
    ]
    assert anonymous == []

    assert GATEWAY_ROUTES <= collected_paths
    gateway_routes = [(p, r) for p, r in routes_with_paths if p.startswith("/api/")]
    assert {p for p, _ in gateway_routes} == GATEWAY_ROUTES
    for full_path, route_or_context in gateway_routes:
        calls = set(_dependency_calls(route_or_context.dependant))
        assert get_gateway_session in calls, f"{full_path} must use get_gateway_session"
        assert get_current_user not in calls, f"{full_path} must not accept dashboard tokens"


def test_unscoped_session_helper_is_gone():
    assert not hasattr(esl.db, "get_session")
