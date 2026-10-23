"""Alembic 執行環境：用 ``db.session`` 的 engine 與 ``db.models`` 的 metadata。

``render_as_batch=True``：SQLite 的 ALTER TABLE 不能改約束或刪欄位，
batch 模式會「建新表、搬資料、換名字」，同一支 migration 在 PostgreSQL 上照常用 ALTER。
"""

from __future__ import annotations

from alembic import context

from db.models import Base
from db.session import get_engine

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=str(get_engine().url),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # 呼叫端（db.session.upgrade_to_head）可能已經給了一條連線，就用那一條
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    with get_engine().connect() as conn:
        _run(conn)


def _run(connection) -> None:  # noqa: ANN001
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
