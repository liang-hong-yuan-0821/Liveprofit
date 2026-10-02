"""Quarantine active BUY suggestions made before rule certification existed.

These rows cannot acquire a historical rule certificate retroactively. Broker-
facing or partly filled rows require reconciliation; untouched suggestions are
superseded. The downgrade intentionally cannot restore a previous live state.
"""

from alembic import op


revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE suggested_orders
           SET status = CASE
                   WHEN status = 'PROPOSED' AND filled_quantity = 0
                   THEN 'SUPERSEDED'
                   ELSE 'RECONCILIATION_REQUIRED'
               END,
               revision = revision + 1,
               updated_at = clock_timestamp()
         WHERE side = 'BUY'
           AND rule_certificate_id IS NULL
           AND status IN ('PROPOSED', 'EXECUTING', 'PARTIALLY_FILLED')
           AND quantity > filled_quantity
    """)


def downgrade() -> None:
    # Reviving orders without current authorization would be unsafe.
    pass
