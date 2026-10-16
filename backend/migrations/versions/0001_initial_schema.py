"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-16
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel
from alembic import op

from app.models import UTCDateTime


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('jobs',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('tax_year', sa.Integer(), nullable=False),
    sa.Column('page_count', sa.Integer(), nullable=False),
    sa.Column('redaction_log', sa.JSON(), nullable=False),
    sa.Column('questionnaire', sa.JSON(), nullable=False),
    sa.Column('error_code', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', UTCDateTime(timezone=True), nullable=False),
    sa.Column('updated_at', UTCDateTime(timezone=True), nullable=False),
    sa.Column('expires_at', UTCDateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_jobs_expires_at'), ['expires_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_user_id'), ['user_id'], unique=False)

    op.create_table('reports',
    sa.Column('job_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('model', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('rules_version', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('findings', sa.JSON(), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('created_at', UTCDateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
    sa.PrimaryKeyConstraint('job_id')
    )


def downgrade() -> None:
    op.drop_table('reports')
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_jobs_user_id'))
        batch_op.drop_index(batch_op.f('ix_jobs_status'))
        batch_op.drop_index(batch_op.f('ix_jobs_expires_at'))

    op.drop_table('jobs')
