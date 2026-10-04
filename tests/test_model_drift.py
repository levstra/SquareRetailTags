from alembic import command
from alembic.config import Config

from tests.conftest import ADMIN_URL
from tests.dbutil import ROOT


def test_models_match_migrated_schema():
    """`alembic check` fails if models and migrations drift apart."""
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", ADMIN_URL)
    command.check(cfg)
