"""jds origin and summary

從 JD 自動搜尋社群面經：``jds.origin``（``user``／``search``）＋ ``jds.summary``（Claude 依原文整理的結論）。

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-24 21:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('jds', schema=None) as batch_op:
        batch_op.add_column(sa.Column('origin', sa.String(length=10), server_default='user', nullable=False))
        batch_op.add_column(sa.Column('summary', sa.Text(), nullable=True))
        batch_op.create_check_constraint('ck_jds_origin', "origin IN ('user', 'search')")


def downgrade() -> None:
    with op.batch_alter_table('jds', schema=None) as batch_op:
        batch_op.drop_constraint('ck_jds_origin', type_='check')
        batch_op.drop_column('summary')
        batch_op.drop_column('origin')
