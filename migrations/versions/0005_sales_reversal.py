"""Preserve reversed reports and allow one active correction per date."""
from pathlib import Path
from alembic import op

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    for statement in Path(__file__).with_suffix('.sql').read_text().split(';'):
        if statement.strip():
            op.execute(statement)


def downgrade():
    raise RuntimeError('Restore a backup instead of removing sales reversal history.')
