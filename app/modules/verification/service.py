from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.core.permissions import STAFF_PLATFORM_ROLES
from app.database import pool
from app.modules.verification.common import (
    DOCUMENT_TYPES,
    MAX_DOCUMENTS,
    USER_EDITABLE_STATUSES,
    USER_UPLOAD_STATUSES,
    load_documents,
    load_individual_sides,
    load_verification_state,
    serialize_state,
)
from app.utils.audit import AuditLogger


async def _audit(conn, user_id: int, action: str, details: dict) -> None:
    await AuditLogger(conn).log_action(
        user_id=user_id,
        actor_id=None,
        company_id=None,
        action=action,
        resource_type="user",
        resource_id=str(user_id),
        details=details,
    )


class IndividualVerificationService:
    def connection(self):
        return pool.connection()

    async def _state(self, conn, user, *, lock: bool = False):
        if user.role in STAFF_PLATFORM_ROLES:
            raise ForbiddenError("Проверка личности доступна только клиентам платформы")
        state = await load_verification_state(conn, user.id, lock=lock)
        if not state:
            raise NotFoundError("Пользователь не найден")
        return state

    async def _payload(self, conn, state) -> dict:
        user_id = state["id"]
        return serialize_state(
            state,
            await load_documents(conn, user_id),
            await load_individual_sides(conn, user_id),
        )

    async def get_status(self, user) -> dict:
        async with self.connection() as conn:
            state = await self._state(conn, user)
            return await self._payload(conn, state)

    async def _check_upload_allowed(self, conn, user, state) -> None:
        if state["status"] not in USER_UPLOAD_STATUSES:
            raise ConflictError("Личность уже подтверждена — документы менять нельзя")
        count = await conn.fetchval("SELECT COUNT(*) FROM user_documents WHERE user_id = $1", user.id)
        if int(count or 0) >= MAX_DOCUMENTS:
            raise ValidationError(f"Можно загрузить не более {MAX_DOCUMENTS} документов")

    async def ensure_can_upload(self, user) -> None:
        async with self.connection() as conn:
            state = await self._state(conn, user)
            await self._check_upload_allowed(conn, user, state)

    async def add_document(self, user, *, doc_type: str, file_name: str | None, file_key: str) -> dict:
        if doc_type not in DOCUMENT_TYPES:
            raise ValidationError("Неизвестный тип документа")
        async with self.connection() as conn:
            async with conn.transaction():
                state = await self._state(conn, user, lock=True)
                await self._check_upload_allowed(conn, user, state)
                document_id = await conn.fetchval(
                    """
                    INSERT INTO user_documents (user_id, doc_type, file_name, file_url, uploaded_by)
                    VALUES ($1, $2, $3, $4, $1)
                    RETURNING id
                    """,
                    user.id,
                    doc_type,
                    (file_name or "")[:255] or None,
                    file_key,
                )
                await _audit(
                    conn,
                    user.id,
                    "verification.individual.document.add",
                    {"document_id": document_id, "doc_type": doc_type, "file_name": file_name},
                )
            return await self._payload(conn, state)

    async def delete_document(self, user, document_id: int) -> dict:
        async with self.connection() as conn:
            async with conn.transaction():
                state = await self._state(conn, user, lock=True)
                if state["status"] not in USER_EDITABLE_STATUSES:
                    raise ConflictError("Документы нельзя удалить, пока заявка на проверке")
                deleted = await conn.fetchrow(
                    """
                    DELETE FROM user_documents WHERE id = $1 AND user_id = $2
                    RETURNING id, doc_type, file_url
                    """,
                    document_id,
                    user.id,
                )
                if not deleted:
                    raise NotFoundError("Документ не найден")
                await _audit(
                    conn,
                    user.id,
                    "verification.individual.document.delete",
                    {"document_id": document_id, "doc_type": deleted["doc_type"]},
                )
            return await self._payload(conn, state)

    async def submit(self, user) -> dict:
        async with self.connection() as conn:
            async with conn.transaction():
                state = await self._state(conn, user, lock=True)
                previous = state["status"]
                if previous not in USER_EDITABLE_STATUSES:
                    raise ConflictError(
                        "Заявка уже на проверке" if previous == "pending" else "Личность уже подтверждена"
                    )
                if not await load_individual_sides(conn, user.id):
                    raise ForbiddenError("Проверка личности доступна только физлицам")
                documents = int(
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM user_documents WHERE user_id = $1", user.id
                    )
                    or 0
                )
                if documents < 1:
                    raise ValidationError("Загрузите хотя бы один документ")
                await conn.execute(
                    """
                    UPDATE users
                    SET individual_verification_status = 'pending',
                        individual_verification_reason = NULL,
                        individual_verification_submitted_at = NOW(),
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    user.id,
                )
                await _audit(
                    conn,
                    user.id,
                    "verification.individual.submit",
                    {"previous_status": previous, "status": "pending", "documents": documents},
                )
            state = await load_verification_state(conn, user.id)
            return await self._payload(conn, state)
