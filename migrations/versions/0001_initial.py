"""Initial shop, recipe, sales and inventory schema."""
from pathlib import Path
from alembic import op

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # This immutable SQL snapshot has no procedural blocks or embedded semicolons.
    sql = Path(__file__).with_suffix('.sql').read_text()
    for statement in sql.split(';'):
        if statement.strip():
            op.execute(statement)


def downgrade():
    raise RuntimeError('Initial schema downgrade is intentionally disabled. Restore a backup instead.')
