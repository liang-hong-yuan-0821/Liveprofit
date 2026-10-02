"""Explicit admission risk scope; never infer a profile for existing accounts."""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("portfolios", sa.Column("risk_profile", sa.String(16), nullable=True))
    op.create_check_constraint("ck_portfolios_risk_profile", "portfolios",
                               "risk_profile IN ('CONSERVATIVE','BALANCED','AGGRESSIVE')")


def downgrade():
    op.drop_constraint("ck_portfolios_risk_profile", "portfolios", type_="check")
    op.drop_column("portfolios", "risk_profile")
