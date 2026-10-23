"""tasks focus stage

重點題組：``tasks.focus_stage``（1 暖身、2 Medium 主力、3 進階）。值由 ``scripts/import_data.py`` 從 tasks.csv 的 focus 欄匯入。

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-23 22:01:06.142285
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('focus_stage', sa.Integer(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_column('focus_stage')

