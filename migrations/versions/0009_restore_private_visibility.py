"""Restore discoverable private courses with approval OR an invite link.

The intermediate archive introduced a hidden invite-only mode. The product now
has the original two choices again. Keep existing course IDs, invitation tokens,
enrollments and request decisions; only normalize the visibility policy.
"""

from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = depends_on = None


def upgrade():
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE course SET is_private=true WHERE access_mode IN ('request', 'invite')"
        )
    )
    connection.execute(
        sa.text("UPDATE course SET access_mode='request' WHERE is_private=true")
    )


def downgrade():
    raise RuntimeError(
        "Visibility rollback cannot infer which courses were hidden. Restore a tested backup instead."
    )
