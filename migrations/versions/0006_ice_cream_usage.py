"""Actual daily ice-cream powder usage and versioned deduction policies."""
from pathlib import Path
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    for statement in Path(__file__).with_suffix('.sql').read_text().split(';'):
        if statement.strip():
            op.execute(statement)


def downgrade():
    raise RuntimeError('Restore a backup rather than deleting powder-usage history.')
