"""Private login sessions and persistent login-attempt limits."""
from pathlib import Path
from alembic import op

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    for statement in Path(__file__).with_suffix('.sql').read_text().split(';'):
        if statement.strip():
            op.execute(statement)


def downgrade():
    op.execute('DROP TABLE auth_login_limits')
    op.execute('DROP TABLE auth_sessions')
