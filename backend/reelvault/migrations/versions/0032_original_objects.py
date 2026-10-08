"""Immutable S3 original references and credential-free namespace identities."""
import sqlalchemy as sa
from alembic import op

revision = '0032'
down_revision = '0031'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('object_namespaces',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('library_id', sa.String(32), nullable=False),
        sa.Column('descriptor', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False))
    op.create_table('original_objects',
        sa.Column('path', sa.String(256), primary_key=True),
        sa.Column('namespace_id', sa.String(32), sa.ForeignKey('object_namespaces.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('key', sa.String(96), nullable=False),
        sa.Column('transfer_id', sa.String(32), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('object_ref', sa.JSON()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('namespace_id', 'key'),
        sa.CheckConstraint("state IN ('pending', 'ready')"),
        sa.CheckConstraint("state != 'ready' OR object_ref IS NOT NULL"))
    op.create_index('ix_original_objects_namespace_id', 'original_objects', ['namespace_id'])


def downgrade() -> None:
    if op.get_bind().execute(sa.text('SELECT COUNT(*) FROM original_objects')).scalar():
        raise RuntimeError('Export S3 originals to local storage before downgrading')
    op.drop_table('original_objects')
    op.drop_table('object_namespaces')
