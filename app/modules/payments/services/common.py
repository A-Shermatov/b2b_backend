from app.modules.payments.schemas import PaymentHistoryItem, PendingPaymentItem

MILESTONE_COLUMNS = """
    m.id, m.payment_plan_id, m.contract_id, m.title, m.percentage, m.amount,
    m.trigger, m.status::text AS status
"""


def history_item(row, *, event: str) -> PaymentHistoryItem:
    return PaymentHistoryItem(
        contract_id=row["contract_id"],
        milestone_id=row["id"],
        title=row["title"],
        amount=row["amount"],
        currency=row["currency"],
        status=row["status"],
        event=event,
        created_at=row["created_at"],
    )


def pending_item(row) -> PendingPaymentItem:
    return PendingPaymentItem(
        contract_id=row["contract_id"],
        milestone_id=row["id"],
        title=row["title"],
        amount=row["amount"],
        currency=row["currency"],
        status=row["status"],
    )
