from fastapi.routing import APIRoute
from sqlalchemy import text

import esl.db
from esl.web.app import app
from esl.web.deps import get_business_session

# Routes that touch no business data, or resolve the business themselves.
UNSCOPED_ROUTES = {"/v1/health", "/v1/webhooks/square"}


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
    # Unwrap _IncludedRouter objects to find actual APIRoute objects with full paths
    routes_with_paths = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            routes_with_paths.append((route.path, route))
        elif hasattr(route, 'original_router') and hasattr(route, 'include_context'):
            # _IncludedRouter: extract the actual routes and add the prefix
            prefix = route.include_context.prefix or ""
            for sub_route in route.original_router.routes:
                if isinstance(sub_route, APIRoute):
                    full_path = prefix + sub_route.path
                    routes_with_paths.append((full_path, sub_route))

    unscoped = [
        full_path
        for full_path, route in routes_with_paths
        if full_path not in UNSCOPED_ROUTES
        and get_business_session not in set(_dependency_calls(route.dependant))
    ]
    assert unscoped == []


def test_unscoped_session_helper_is_gone():
    assert not hasattr(esl.db, "get_session")
