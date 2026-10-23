"""baseline v0.1-core schema

導入 Alembic 時的現況（7 張表，含 2026-10-23 補的 plan_items.reason_source）。
導入前就存在的資料庫不跑這支，由 db.session 直接 stamp 成 0001。

Revision ID: 0001
Revises: 
Create Date: 2026-10-23 17:54:08.592542
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('topics',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('domain', sa.String(length=20), nullable=False),
    sa.Column('base_weight', sa.Integer(), nullable=False),
    sa.Column('prereq', sa.JSON(), nullable=False),
    sa.Column('key_points', sa.JSON(), nullable=False),
    sa.CheckConstraint('base_weight BETWEEN 1 AND 10', name='ck_topics_base_weight'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('users',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('display_name', sa.String(length=80), nullable=False),
    sa.Column('daily_api_quota', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('plans',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('goal_text', sa.Text(), nullable=False),
    sa.Column('parsed_goal', sa.JSON(), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('days', sa.Integer(), nullable=False),
    sa.Column('daily_minutes', sa.Integer(), nullable=False),
    sa.Column('level', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("level IN ('familiar', 'normal', 'new')", name='ck_plans_level'),
    sa.CheckConstraint('daily_minutes > 0', name='ck_plans_daily_minutes'),
    sa.CheckConstraint('days > 0', name='ck_plans_days'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('plans', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_plans_user_id'), ['user_id'], unique=False)

    op.create_table('tasks',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('source', sa.String(length=10), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('url', sa.String(length=500), nullable=False),
    sa.Column('topic_id', sa.String(length=40), nullable=False),
    sa.Column('prereq', sa.String(length=40), nullable=True),
    sa.Column('difficulty', sa.String(length=10), nullable=False),
    sa.Column('est_minutes', sa.Integer(), nullable=False),
    sa.Column('static_note', sa.Text(), nullable=True),
    sa.Column('task_type', sa.String(length=20), nullable=False),
    sa.CheckConstraint("difficulty IN ('Easy', 'Medium', 'Hard')", name='ck_tasks_difficulty'),
    sa.CheckConstraint('est_minutes > 0', name='ck_tasks_est_minutes'),
    sa.ForeignKeyConstraint(['prereq'], ['topics.id'], ),
    sa.ForeignKeyConstraint(['topic_id'], ['topics.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('url', name='uq_tasks_url')
    )
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tasks_topic_id'), ['topic_id'], unique=False)

    op.create_table('plan_items',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('plan_id', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.Integer(), nullable=False),
    sa.Column('scheduled_date', sa.Date(), nullable=False),
    sa.Column('order_in_day', sa.Integer(), nullable=False),
    sa.Column('priority', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('done_at', sa.DateTime(), nullable=True),
    sa.Column('actual_minutes', sa.Integer(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('reason_source', sa.String(length=10), nullable=True),
    sa.Column('key_points', sa.JSON(), nullable=False),
    sa.CheckConstraint("priority IN ('must', 'recommended', 'bonus')", name='ck_plan_items_priority'),
    sa.CheckConstraint("status IN ('todo', 'done', 'skipped')", name='ck_plan_items_status'),
    sa.ForeignKeyConstraint(['plan_id'], ['plans.id'], ),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('plan_id', 'task_id', name='uq_plan_items_plan_task')
    )
    with op.batch_alter_table('plan_items', schema=None) as batch_op:
        batch_op.create_index('ix_plan_items_plan_date', ['plan_id', 'scheduled_date'], unique=False)
        batch_op.create_index(batch_op.f('ix_plan_items_plan_id'), ['plan_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_plan_items_scheduled_date'), ['scheduled_date'], unique=False)

    op.create_table('task_completions',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.Integer(), nullable=False),
    sa.Column('done_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'task_id', name='uq_task_completions_user_task')
    )
    with op.batch_alter_table('task_completions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_task_completions_user_id'), ['user_id'], unique=False)

    op.create_table('progress_events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('plan_id', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=True),
    sa.Column('event', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.CheckConstraint("event IN ('done', 'undo', 'replan')", name='ck_progress_events_event'),
    sa.ForeignKeyConstraint(['item_id'], ['plan_items.id'], ),
    sa.ForeignKeyConstraint(['plan_id'], ['plans.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('progress_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_progress_events_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_progress_events_plan_id'), ['plan_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_progress_events_user_id'), ['user_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('progress_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_progress_events_user_id'))
        batch_op.drop_index(batch_op.f('ix_progress_events_plan_id'))
        batch_op.drop_index(batch_op.f('ix_progress_events_created_at'))

    op.drop_table('progress_events')
    with op.batch_alter_table('task_completions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_task_completions_user_id'))

    op.drop_table('task_completions')
    with op.batch_alter_table('plan_items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_plan_items_scheduled_date'))
        batch_op.drop_index(batch_op.f('ix_plan_items_plan_id'))
        batch_op.drop_index('ix_plan_items_plan_date')

    op.drop_table('plan_items')
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tasks_topic_id'))

    op.drop_table('tasks')
    with op.batch_alter_table('plans', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_plans_user_id'))

    op.drop_table('plans')
    op.drop_table('users')
    op.drop_table('topics')
