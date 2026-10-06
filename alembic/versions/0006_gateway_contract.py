"""gateway contract: store codes, tag versions, currency, command queue, telemetry

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-04
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

CURRENT_BUSINESS = "nullif(current_setting('app.business_id', true), '')::uuid"

RLS_TABLES = {
    "gateways": "SELECT, UPDATE",  # inserts/revokes come from the operator CLI
    "label_updates": "SELECT, INSERT, UPDATE",
    "label_telemetry": "SELECT, INSERT, UPDATE",
    "access_point_status": "SELECT, INSERT, UPDATE",
}

# Both cross RLS before a business is known (step-2 pattern; see
# docs/superpowers/plans/2026-10-04-cross-step-interfaces.md).
GATEWAY_FOR_KEY = """
CREATE FUNCTION esl_gateway_for_key(p_prefix text)
RETURNS TABLE (gateway_id uuid, business_id uuid, store_id uuid, store_code text,
               key_hash text, revoked_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT g.id, g.business_id, g.store_id, s.code, g.key_hash, g.revoked_at
  FROM gateways g
  JOIN stores s ON s.id = g.store_id AND s.business_id = g.business_id
  WHERE g.key_prefix = p_prefix
$$
"""

OPEN_COMMANDS = """
CREATE FUNCTION esl_businesses_with_open_commands()
RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT DISTINCT business_id FROM label_updates WHERE status = 'Dispatched'
$$
"""


def _store_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["business_id", "store_id"], ["stores.business_id", "stores.id"],
        ondelete="CASCADE", name=f"{table}_store_fkey",
    )


def _business_id() -> sa.Column:
    return sa.Column("business_id", UUID(as_uuid=True),
                     sa.ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)


def upgrade() -> None:
    op.add_column("stores", sa.Column("code", sa.Text, nullable=True))
    op.create_unique_constraint("stores_code_key", "stores", ["code"])

    op.add_column("tags", sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("0")))
    op.drop_column("tags", "battery_pct")
    op.drop_column("tags", "last_pushed_at")

    op.add_column("catalog_variations", sa.Column("currency", sa.Text, nullable=True))
    # All accounts so far are US; the next sync overwrites this from Square.
    # catalog_variations FORCEs RLS: lift it for this statement only (see 0004/0005)
    # so a non-superuser, non-BYPASSRLS owner doesn't silently match zero rows.
    op.execute("ALTER TABLE catalog_variations NO FORCE ROW LEVEL SECURITY")
    op.execute("UPDATE catalog_variations SET currency = 'USD' WHERE price IS NOT NULL")
    op.execute("ALTER TABLE catalog_variations FORCE ROW LEVEL SECURITY")

    op.create_table(
        "gateways",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        _business_id(),
        sa.Column("store_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("key_prefix", sa.Text, nullable=False, unique=True),
        sa.Column("key_hash", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True)),
        _store_fk("gateways"),
    )

    op.create_table(
        "label_updates",
        sa.Column("update_id", UUID(as_uuid=True), primary_key=True),
        _business_id(),
        sa.Column("store_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tag_id", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("version", sa.Integer),
        sa.Column("product_id", sa.Text),
        sa.Column("display", JSONB),
        sa.Column("seconds", sa.Integer),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'Pending'")),
        sa.Column("status_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("dispatched_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("final_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("not_before", sa.TIMESTAMP(timezone=True)),
        sa.Column("attempt", sa.Integer),
        sa.Column("ap_id", sa.Text),
        sa.Column("failure_code", sa.Text),
        sa.Column("failure_message", sa.Text),
        sa.Column("retryable", sa.Boolean),
        sa.Column("diagnostics", JSONB),
        sa.Column("repend_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        _store_fk("label_updates"),
        sa.CheckConstraint("action in ('Update', 'Unassign', 'Locate')", name="label_updates_action_check"),
        sa.CheckConstraint(
            "status in ('Pending', 'Dispatched', 'Succeeded', 'Failed', 'Superseded')",
            name="label_updates_status_check",
        ),
        # NULL versions (Locate) never collide: Postgres treats NULLs as distinct.
        sa.UniqueConstraint("business_id", "tag_id", "version", name="label_updates_tag_version_key"),
    )
    op.create_index("label_updates_store_status", "label_updates", ["store_id", "status", "created_at"])
    op.create_index("label_updates_tag", "label_updates", ["business_id", "tag_id", "created_at"])

    op.create_table(
        "label_telemetry",
        sa.Column("business_id", UUID(as_uuid=True),
                  sa.ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("store_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tag_id", sa.Text, primary_key=True),
        sa.Column("first_seen_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("last_seen_ap_id", sa.Text),
        sa.Column("rf_power", sa.Integer),
        sa.Column("battery", sa.Integer),
        sa.Column("low_battery", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("temperature", sa.Integer),
        sa.Column("status_byte", sa.Integer),
        sa.Column("firmware_version", sa.Integer),
        sa.Column("last_success_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("last_result_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("last_result_success", sa.Boolean),
        sa.Column("raw", JSONB, nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        _store_fk("label_telemetry"),
    )

    op.create_table(
        "access_point_status",
        sa.Column("business_id", UUID(as_uuid=True),
                  sa.ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("store_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("ap_id", sa.Text, primary_key=True),
        sa.Column("connected", sa.Boolean, nullable=False),
        sa.Column("stale", sa.Boolean, nullable=False),
        sa.Column("connected_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("disconnected_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("last_heartbeat_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("heartbeat_interval_seconds", sa.Integer, nullable=False),
        sa.Column("ap_version", sa.Text),
        sa.Column("ip", sa.Text),
        sa.Column("wait_count", sa.Integer, nullable=False),
        sa.Column("send_count", sa.Integer, nullable=False),
        sa.Column("reported_at", sa.TIMESTAMP(timezone=True), nullable=False),
        _store_fk("access_point_status"),
    )

    for table, privileges in RLS_TABLES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY business_isolation ON {table} "
            f"USING (business_id = {CURRENT_BUSINESS}) "
            f"WITH CHECK (business_id = {CURRENT_BUSINESS})"
        )
        op.execute(f"GRANT {privileges} ON {table} TO esl_app")

    for sql, signature in ((GATEWAY_FOR_KEY, "esl_gateway_for_key(text)"),
                           (OPEN_COMMANDS, "esl_businesses_with_open_commands()")):
        op.execute(sql)
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO esl_app")


def downgrade() -> None:
    """Destructive, and effectively one-way once a Pi has received commands.

    Drops the command queue and tags.version. Versions then restart at 0, while the
    Pi keeps the highest version it applied per tag, so it would report every later
    update as Superseded and no tag would change until each passed its old version.
    """
    op.execute("DROP FUNCTION esl_businesses_with_open_commands()")
    op.execute("DROP FUNCTION esl_gateway_for_key(text)")
    for table in ("access_point_status", "label_telemetry", "label_updates", "gateways"):
        op.drop_table(table)
    op.drop_column("catalog_variations", "currency")
    op.add_column("tags", sa.Column("last_pushed_at", sa.TIMESTAMP(timezone=True)))
    op.add_column("tags", sa.Column("battery_pct", sa.SmallInteger))
    op.drop_column("tags", "version")
    op.drop_constraint("stores_code_key", "stores", type_="unique")
    op.drop_column("stores", "code")
