from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

from .config import PACKAGE_DIR


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(PACKAGE_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def upgrade(engine: Engine) -> None:
    cfg = alembic_config(str(engine.url))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
