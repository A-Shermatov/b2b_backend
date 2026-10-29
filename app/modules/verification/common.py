from app.core.exceptions import ValidationError
from app.utils.storage import public_file_url

IDENTITY_MIME_TYPES = ("application/pdf", "image/jpeg", "image/png", "image/webp")

INDIVIDUAL_STATUSES = ("none", "pending", "needs_documents", "verified", "rejected")
QUEUE_STATUSES = ("pending", "needs_documents")
USER_EDITABLE_STATUSES = ("none", "needs_documents", "rejected")
USER_UPLOAD_STATUSES = USER_EDITABLE_STATUSES + ("pending",)
DOCUMENT_TYPES = ("passport", "selfie", "patent", "other")
MAX_DOCUMENTS = 10

INDIVIDUAL_STATUS_LABELS = {
    "none": "Не проверен",
    "pending": "На проверке",
    "needs_documents": "Нужны документы",
    "verified": "Проверен",
    "rejected": "Отклонено",
}

DOCUMENT_TYPE_LABELS = {
    "passport": "Паспорт",
    "selfie": "Селфи с документом",
    "patent": "Патент / регистрация ИП",
    "other": "Другой документ",
}

VERIFICATION_COLUMNS = """
    individual_verification_status AS status,
    individual_verification_reason AS reason,
    individual_verification_submitted_at AS submitted_at,
    individual_verified_at AS verified_at,
    individual_verified_by AS verified_by
"""


def actor_verification_sql(actor: str = "a", company: str = "c") -> str:
    """Public verification status of an actor: company status or the owner's identity check.

    Individuals that never applied are exposed as ``unverified`` (not ``pending``).
    """
    return f"""
        CASE
            WHEN {actor}.kind::text = 'company' THEN {company}.verification_status::text
            ELSE (
                SELECT CASE iv.individual_verification_status
                    WHEN 'none' THEN 'unverified'
                    ELSE iv.individual_verification_status
                END
                FROM users iv WHERE iv.id = {actor}.user_id
            )
        END
    """


def ensure_identity_file_type(content_type: str | None) -> None:
    if content_type not in IDENTITY_MIME_TYPES:
        raise ValidationError("Загрузите PDF или изображение (JPG, PNG, WEBP)")


def trust_level_for_individual(status: str | None) -> str:
    return "verified" if status == "verified" else "basic"


async def sync_individual_trust_level(conn, user_id: int, status: str) -> None:
    await conn.execute(
        """
        UPDATE actors
        SET trust_level = $1::trust_level, updated_at = NOW()
        WHERE kind::text = 'individual' AND user_id = $2
        """,
        trust_level_for_individual(status),
        user_id,
    )


def serialize_document(row) -> dict:
    return {
        "id": row["id"],
        "doc_type": row["doc_type"],
        "doc_type_label": DOCUMENT_TYPE_LABELS.get(row["doc_type"], row["doc_type"]),
        "file_name": row["file_name"],
        "file_url": public_file_url(row["file_url"]) if row["file_url"] else None,
        "status": row["status"],
        "uploaded_at": row["uploaded_at"],
        "uploaded_by_admin": bool(row["uploaded_by"]) and row["uploaded_by"] != row["user_id"],
    }


async def load_documents(conn, user_id: int) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT id, user_id, doc_type, file_name, file_url, status, uploaded_by, uploaded_at
        FROM user_documents
        WHERE user_id = $1
        ORDER BY uploaded_at DESC, id DESC
        """,
        user_id,
    )
    return [serialize_document(row) for row in rows]


async def load_individual_sides(conn, user_id: int) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT DISTINCT side::text AS side FROM actors
        WHERE kind::text = 'individual' AND user_id = $1 AND is_active = TRUE
        ORDER BY 1
        """,
        user_id,
    )
    return [row["side"] for row in rows]


async def load_verification_state(conn, user_id: int, *, lock: bool = False):
    return await conn.fetchrow(
        f"""
        SELECT id, email, first_name, last_name, role::text AS role, {VERIFICATION_COLUMNS}
        FROM users WHERE id = $1
        {"FOR UPDATE" if lock else ""}
        """,
        user_id,
    )


def serialize_state(state, documents: list[dict], sides: list[str]) -> dict:
    status = state["status"] or "none"
    return {
        "user_id": state["id"],
        "status": status,
        "status_label": INDIVIDUAL_STATUS_LABELS.get(status, status),
        "reason": state["reason"],
        "submitted_at": state["submitted_at"],
        "verified_at": state["verified_at"],
        "sides": sides,
        "is_individual": bool(sides),
        "documents": documents,
        "can_edit_documents": status in USER_EDITABLE_STATUSES,
        "can_upload": status in USER_UPLOAD_STATUSES and len(documents) < MAX_DOCUMENTS,
        "can_submit": status in USER_EDITABLE_STATUSES and bool(documents) and bool(sides),
    }
