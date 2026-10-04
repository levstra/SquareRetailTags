import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.seed import BUSINESS_A, BUSINESS_B, BUSINESS_TABLES, STORE_A, TAG_A, owner_column

pytestmark = pytest.mark.usefixtures("two_businesses")


async def _scope(conn, business_id):
    await conn.execute(
        text("select set_config('app.business_id', :id, false)"), {"id": str(business_id)}
    )


async def test_app_login_is_not_privileged(app_conn):
    row = (await app_conn.execute(text(
        "select rolsuper, rolbypassrls from pg_roles where rolname = current_user"
    ))).one()
    assert row == (False, False)


@pytest.mark.parametrize("table", BUSINESS_TABLES)
async def test_unscoped_sees_nothing(app_conn, table):
    count = (await app_conn.execute(text(f"select count(*) from {table}"))).scalar_one()
    assert count == 0


@pytest.mark.parametrize("table", BUSINESS_TABLES)
async def test_scoped_sees_only_own_rows(app_conn, table):
    await _scope(app_conn, BUSINESS_A)
    col = owner_column(table)
    rows = (await app_conn.execute(text(f"select {col} from {table}"))).scalars().all()
    assert rows, f"expected business A rows in {table}"
    assert set(rows) == {BUSINESS_A}


async def test_cannot_insert_for_other_business(app_conn):
    await _scope(app_conn, BUSINESS_A)
    with pytest.raises(DBAPIError, match="row-level security"):
        await app_conn.execute(text(
            "insert into catalog_items (business_id, id, name, updated_at) "
            "values (:b, 'SNEAKY', 'x', now())"
        ), {"b": BUSINESS_B})


async def test_cannot_move_row_to_other_business(app_conn):
    await _scope(app_conn, BUSINESS_A)
    with pytest.raises(DBAPIError):
        await app_conn.execute(
            text("update tags set business_id = :b where id = :t"), {"b": BUSINESS_B, "t": TAG_A}
        )


async def test_unscoped_insert_fails(app_conn):
    with pytest.raises(DBAPIError, match="row-level security"):
        await app_conn.execute(text(
            "insert into catalog_items (business_id, id, name, updated_at) "
            "values (:a, 'NOSCOPE', 'x', now())"
        ), {"a": BUSINESS_A})


async def test_app_cannot_modify_businesses_or_stores(app_conn):
    await _scope(app_conn, BUSINESS_A)
    with pytest.raises(DBAPIError, match="permission denied"):
        await app_conn.execute(text("update businesses set name = 'x'"))


async def test_tag_cannot_reference_other_business_variation(admin_session):
    with pytest.raises(DBAPIError, match="foreign key"):
        await admin_session.execute(text(
            "insert into tags (id, business_id, store_id, variation_id) "
            "values ('CCCCCCCCCCCCCCC1', :a, :sa, 'VAR_B')"
        ), {"a": BUSINESS_A, "sa": STORE_A})


async def test_deleting_variation_unassigns_tag_but_keeps_business(admin_session):
    await admin_session.execute(text("delete from catalog_variations where id = 'VAR_A'"))
    row = (await admin_session.execute(
        text("select business_id, variation_id from tags where id = :t"), {"t": TAG_A}
    )).one()
    assert row == (BUSINESS_A, None)
