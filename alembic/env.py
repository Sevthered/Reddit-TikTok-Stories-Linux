import os
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# No ORM models -- this app is pure sqlite3 stdlib (core/db.py), and
# introducing SQLAlchemy models just to get --autogenerate would create a
# second, parallel "shadow schema" to keep in sync by hand. Migrations here
# are hand-authored op.execute()/op.batch_alter_table() scripts instead; see
# wiki/decisions/2026-07-03-alembic-manual-migrations.md.
target_metadata = None

# Resolve the DB path the same way core/db.py's default does (repo-root
# relative data/used_stories.db), overridable via ALEMBIC_DB_PATH for
# testing against a scratch copy without touching the real file.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = os.environ.get("ALEMBIC_DB_PATH", str(_REPO_ROOT / "data" / "used_stories.db"))
config.set_main_option("sqlalchemy.url", f"sqlite:///{_DB_PATH}")

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite's ALTER TABLE is very limited (rename/add-column only) --
        # render_as_batch makes op.batch_alter_table() do the 12-step
        # table-rebuild dance for anything else. See the decision note.
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
