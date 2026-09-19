from app.modules.notifications.schemas import NotificationSchema

NOTIFICATION_COLUMNS = """
    id,
    type::text AS type,
    title,
    body,
    href,
    read,
    created_at
"""


def notification_type_value(ntype) -> str:
    return ntype.value if hasattr(ntype, "value") else str(ntype)


def shape_notification_row(row) -> NotificationSchema:
    return NotificationSchema.model_validate(dict(row))
