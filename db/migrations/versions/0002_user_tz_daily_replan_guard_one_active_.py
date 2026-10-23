"""user tz, daily replan guard, one active plan, api usage

- ``users.tz``：「今天」照使用者時區算。
- ``plans.last_replanned_on``：每份計畫每天只檢查一次要不要重排。
- ``uq_plans_one_active_per_user``：資料庫層保證同時只有一份進行中的計畫。
- ``api_usage``：Claude 呼叫次數改存資料庫（原本在記憶體）。
- **資料**：``plan_items.done_at`` 從「伺服器本地時間」改成 UTC，跟其他時間欄位一致。

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23 17:54:49.598652
"""

from __future__ import annotations

import datetime as _dt

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_usage",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.CheckConstraint("count >= 0", name="ck_api_usage_count"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id", "day", "kind"),
    )

    _archive_extra_active_plans()
    with op.batch_alter_table("plans", schema=None) as batch_op:
        batch_op.add_column(sa.Column("last_replanned_on", sa.Date(), nullable=True))
        batch_op.create_index(
            "uq_plans_one_active_per_user",
            ["user_id"],
            unique=True,
            sqlite_where=sa.text("status = 'active'"),
            postgresql_where=sa.text("status = 'active'"),
        )

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("tz", sa.String(length=40), server_default="Asia/Taipei", nullable=False)
        )

    _done_at_local_to_utc()


def downgrade() -> None:
    _done_at_utc_to_local()
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("tz")

    with op.batch_alter_table("plans", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_plans_one_active_per_user",
            sqlite_where=sa.text("status = 'active'"),
            postgresql_where=sa.text("status = 'active'"),
        )
        batch_op.drop_column("last_replanned_on")

    op.drop_table("api_usage")


def _archive_extra_active_plans() -> None:
    """唯一索引建得起來的前提：每人最多一份 active。多的只留最新的，其餘收起來（不刪）。"""
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, user_id FROM plans WHERE status = 'active' ORDER BY created_at DESC, id DESC")
    ).all()
    seen: set[int] = set()
    for plan_id, user_id in rows:
        if user_id in seen:
            conn.execute(sa.text("UPDATE plans SET status = 'archived' WHERE id = :id"), {"id": plan_id})
        seen.add(user_id)


def _convert_done_at(to_utc: bool) -> None:
    """``plan_items.done_at`` 原本用 ``datetime.now()`` 寫入，是**跑網站那台機器**的本地時間。

    這支 migration 在同一台機器上跑，所以用這台機器的時區換算。其他時間欄位
    （``created_at``、``task_completions.done_at``）一直是 ``func.now()``＝UTC，不用動。
    """
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, done_at FROM plan_items WHERE done_at IS NOT NULL")).all()
    for item_id, raw in rows:
        stamp = raw if isinstance(raw, _dt.datetime) else _dt.datetime.fromisoformat(str(raw))
        if to_utc:
            new = stamp.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        else:
            new = stamp.replace(tzinfo=_dt.timezone.utc).astimezone().replace(tzinfo=None)
        conn.execute(
            sa.text("UPDATE plan_items SET done_at = :at WHERE id = :id"),
            # 存成 SQLAlchemy 在 SQLite 用的字串格式（直接傳 datetime 會用到 3.12 起棄用的轉接器）
            {"at": new.strftime("%Y-%m-%d %H:%M:%S.%f"), "id": item_id},
        )


def _done_at_local_to_utc() -> None:
    _convert_done_at(to_utc=True)


def _done_at_utc_to_local() -> None:
    _convert_done_at(to_utc=False)
