"""Store the daily lifecycle execution projection separately from rule inputs."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("position_daily_facts", sa.Column("planning_result", JSONB(), nullable=True))


def downgrade():
    op.drop_column("position_daily_facts", "planning_result")
