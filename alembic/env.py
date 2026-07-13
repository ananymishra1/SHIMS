from logging.config import fileConfig
from pathlib import Path
import sys

from sqlalchemy import create_engine, pool
from alembic import context

# Ensure project root is on path so shared.config can be imported
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.config import settings  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# SHIMS uses raw SQLite SQL (not SQLAlchemy ORM), so we do not have a
# MetaData object for autogenerate. Migrations are hand-written to match
# the schema defined in shared/database.py and related schema modules.
target_metadata = None


def get_database_url() -> str:
    db_path = settings.database_path
    db_str = str(db_path)
    if db_str.startswith(('postgresql://', 'postgres://', 'mysql://')):
        return db_str
    return f"sqlite:///{db_path}"


def run_migrations_offline() -> None:
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = get_database_url()
    connectable = create_engine(url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
