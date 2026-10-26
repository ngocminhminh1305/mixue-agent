"""Draft revisions and source-photo links; retain overlapping extracted rows."""
from pathlib import Path
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    for statement in Path(__file__).with_suffix('.sql').read_text().split(';'):
        if statement.strip():
            op.execute(statement)


def downgrade():
    raise RuntimeError('Restore a backup instead of discarding saved draft provenance.')
