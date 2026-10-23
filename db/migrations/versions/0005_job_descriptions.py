"""job descriptions

JD 出處引用：``jds`` 存使用者貼上的職缺描述原文（分析結果每次從原文算，不存）。

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-24 10:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('jds',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('jds', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_jds_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('jds', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_jds_user_id'))

    op.drop_table('jds')
