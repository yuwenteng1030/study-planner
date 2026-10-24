"""jds kind and source url

社群面經：``jds.kind``（``jd``／``post``）＋ ``jds.source_url``。舊資料都是職缺描述，預設 ``jd``。

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-24 18:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('jds', schema=None) as batch_op:
        batch_op.add_column(sa.Column('kind', sa.String(length=10), server_default='jd', nullable=False))
        batch_op.add_column(sa.Column('source_url', sa.String(length=500), nullable=True))
        batch_op.create_check_constraint('ck_jds_kind', "kind IN ('jd', 'post')")


def downgrade() -> None:
    with op.batch_alter_table('jds', schema=None) as batch_op:
        batch_op.drop_constraint('ck_jds_kind', type_='check')
        batch_op.drop_column('source_url')
        batch_op.drop_column('kind')
