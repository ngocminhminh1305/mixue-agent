"""Inventory operations, physical-count baselines and warning thresholds."""
from pathlib import Path
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    for statement in Path(__file__).with_suffix('.sql').read_text().split(';'):
        if statement.strip():
            op.execute(statement)


def downgrade():
    raise RuntimeError('Restore a backup instead of deleting inventory count history.')
