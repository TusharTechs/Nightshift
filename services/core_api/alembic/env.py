from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool

from app.config import get_settings
from app.infrastructure.database.session import Base
from app.infrastructure.database import models  # noqa: F401  (registers metadata)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)


# Alembic's own default for `alembic_version.version_num` is VARCHAR(32) —
# this project's revision ids follow a `NNNN_sprintN_<description>` slug
# convention that has already exceeded 32 chars twice (0003, then 0004; both
# had to have their revision id manually shortened, filename left
# untouched). Widening this here only affects a freshly-created
# alembic_version table (e.g. a new clone's first `upgrade head`) — an
# EXISTING database's column stays whatever width it was created with, so
# this doesn't retroactively fix already-initialized environments.
VERSION_TABLE_COLUMN_LENGTH = 255


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        version_table_column_length=VERSION_TABLE_COLUMN_LENGTH,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_column_length=VERSION_TABLE_COLUMN_LENGTH,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
