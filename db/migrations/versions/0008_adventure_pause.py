"""adventure pause

冒險計時可以暫停：``active_adventures`` 加上 ``paused_at``（暫停中的話，按暫停的時間）與
``paused_seconds``（之前幾次暫停加起來的秒數）。只加欄位，既有的計時照舊。

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-26 11:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('active_adventures', schema=None) as batch_op:
        batch_op.add_column(sa.Column('paused_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('paused_seconds', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('active_adventures', schema=None) as batch_op:
        batch_op.drop_column('paused_seconds')
        batch_op.drop_column('paused_at')
