"""adventure timer

冒險計時：``active_adventures``（每位使用者同時一場，記開始時間）＋ ``task_completions.actual_minutes``（地圖上完成的題目也能記實際分鐘數）。

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-23 23:37:09.332353
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('active_adventures',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('user_id')
    )
    with op.batch_alter_table('task_completions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('actual_minutes', sa.Integer(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('task_completions', schema=None) as batch_op:
        batch_op.drop_column('actual_minutes')

    op.drop_table('active_adventures')
