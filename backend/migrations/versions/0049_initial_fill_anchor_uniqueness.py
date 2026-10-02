"""Make a reported first fill the anchor of at most one lifecycle."""

from alembic import op
import sqlalchemy as sa


revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


_CONSTRAINT = "uq_position_lifecycle_initial_fill"


def upgrade() -> None:
    # Hold writers out between the duplicate preflight and constraint creation.
    op.execute("LOCK TABLE position_lifecycle_states IN SHARE ROW EXCLUSIVE MODE")
    duplicate_groups = op.get_bind().scalar(sa.text("""
        SELECT count(*)
          FROM (
                SELECT initial_fill_id
                  FROM position_lifecycle_states
                 WHERE initial_fill_id IS NOT NULL
                 GROUP BY initial_fill_id
                HAVING count(*) > 1
               ) duplicates
    """))
    if duplicate_groups:
        raise RuntimeError(
            f"initial fill anchor uniqueness preflight failed: "
            f"{duplicate_groups} duplicate group(s); resolve historical ownership before 0049"
        )
    # PostgreSQL permits multiple NULL values in a UNIQUE constraint.
    op.create_unique_constraint(
        _CONSTRAINT, "position_lifecycle_states", ["initial_fill_id"])


def downgrade() -> None:
    # Dropping the constraint does not alter lifecycle or fill facts.
    op.drop_constraint(_CONSTRAINT, "position_lifecycle_states", type_="unique")
