"""durable observation sequence

Revision ID: 0003_durable_observation_sequence
Revises: 0002_collector_identity
Create Date: 2026-07-20 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_durable_observation_sequence"
down_revision = "0002_collector_identity"
branch_labels = None
depends_on = None

BATCH_SIZE = 1000


def upgrade() -> None:
    op.create_table(
        "observation_ingest_sequence_state",
        sa.Column("singleton_id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), server_default="0", nullable=False),
        sa.CheckConstraint("singleton_id = 1", name="ck_observation_ingest_sequence_state_singleton"),
        sa.CheckConstraint("last_sequence >= 0", name="ck_observation_ingest_sequence_state_nonnegative"),
        sa.PrimaryKeyConstraint("singleton_id"),
    )
    op.create_table(
        "observation_ingest_entries",
        sa.Column("ingest_sequence", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("observation_id", sa.String(length=128), nullable=False),
        sa.Column("inserted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "observation_id"], ["observations.tenant_id", "observations.observation_id"]),
        sa.PrimaryKeyConstraint("ingest_sequence"),
        sa.UniqueConstraint("tenant_id", "observation_id", name="uq_observation_ingest_entries_observation"),
    )
    op.create_index("ix_observation_ingest_entries_tenant_sequence", "observation_ingest_entries", ["tenant_id", "ingest_sequence"])
    connection = op.get_bind()
    metadata = sa.MetaData()
    observations = sa.Table("observations", metadata, sa.Column("tenant_id", sa.String(128)), sa.Column("observation_id", sa.String(128)), sa.Column("inserted_at", sa.DateTime(timezone=True)))
    entries = sa.Table("observation_ingest_entries", metadata, sa.Column("ingest_sequence", sa.BigInteger()), sa.Column("tenant_id", sa.String(128)), sa.Column("observation_id", sa.String(128)))
    sequence = 0
    last_key = None
    while True:
        stmt = sa.select(observations.c.tenant_id, observations.c.observation_id, observations.c.inserted_at).order_by(observations.c.inserted_at.asc(), observations.c.tenant_id.asc(), observations.c.observation_id.asc()).limit(BATCH_SIZE)
        if last_key is not None:
            last_inserted_at, last_tenant_id, last_observation_id = last_key
            stmt = stmt.where(sa.or_(observations.c.inserted_at > last_inserted_at, sa.and_(observations.c.inserted_at == last_inserted_at, observations.c.tenant_id > last_tenant_id), sa.and_(observations.c.inserted_at == last_inserted_at, observations.c.tenant_id == last_tenant_id, observations.c.observation_id > last_observation_id)))
        rows = connection.execute(stmt).fetchall()
        if not rows:
            break
        payload = []
        for row in rows:
            sequence += 1
            payload.append({"ingest_sequence": sequence, "tenant_id": row.tenant_id, "observation_id": row.observation_id})
        connection.execute(entries.insert(), payload)
        final = rows[-1]
        last_key = (final.inserted_at, final.tenant_id, final.observation_id)
    connection.execute(sa.text("INSERT INTO observation_ingest_sequence_state (singleton_id, last_sequence) VALUES (1, :last_sequence)"), {"last_sequence": sequence})


def downgrade() -> None:
    op.drop_index("ix_observation_ingest_entries_tenant_sequence", table_name="observation_ingest_entries")
    op.drop_table("observation_ingest_entries")
    op.drop_table("observation_ingest_sequence_state")
