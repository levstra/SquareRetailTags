from sqlalchemy import text


async def test_every_rls_table_has_admin_policy(admin_session):
    rows = (await admin_session.execute(text("""
        select t.tablename,
               exists (select 1 from pg_policies p
                       where p.schemaname = 'public' and p.tablename = t.tablename
                         and p.policyname = 'admin_all'
                         and p.roles = '{esl_admin}'::name[]
                         and p.permissive = 'PERMISSIVE'
                         and p.qual = 'true' and p.with_check = 'true') as ok
        from pg_tables t
        where t.schemaname = 'public' and t.rowsecurity
    """))).all()
    assert len(rows) >= 8, "expected the RLS tables from steps 1-4"
    assert [r.tablename for r in rows if not r.ok] == []


async def test_esl_admin_role_cannot_bypass_rls_by_attribute(admin_session):
    row = (await admin_session.execute(text(
        "select rolsuper, rolbypassrls from pg_roles where rolname = 'esl_admin'"
    ))).one()
    assert row == (False, False)
