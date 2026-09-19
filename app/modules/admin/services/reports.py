from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.modules.admin.services.common import (
    actor_id_of,
    actor_role_of,
    file_url,
    load_owners,
    log_admin,
    normalize_query,
    notify_user,
    serialize_audit_entry,
    with_reason,
)

REPORT_REASONS = ("spam", "fraud", "counterfeit", "abuse", "other")
REPORT_TARGET_TYPES = ("catalog", "rfq", "proposal")


def _reports_union_sql(view: str, query: str | None) -> tuple[str, list]:
    reason_filter = None if view == "all" else view
    normalized = normalize_query(query)
    params: list = []
    reason_sql = ""
    if reason_filter:
        params.append(reason_filter)
        reason_sql = f" AND reason::text = ${len(params)}"

    search_catalog = search_rfq = search_proposal = ""
    if normalized:
        params.append(f"%{normalized}%")
        idx = len(params)
        search_catalog = f"""
            AND (
                ci.title ILIKE ${idx}
                OR r.details ILIKE ${idx}
                OR u.email ILIKE ${idx}
                OR u.first_name ILIKE ${idx}
                OR u.last_name ILIKE ${idx}
                OR r.id::text ILIKE ${idx}
            )
        """
        search_rfq = f"""
            AND (
                rfq.title ILIKE ${idx}
                OR r.details ILIKE ${idx}
                OR u.email ILIKE ${idx}
                OR u.first_name ILIKE ${idx}
                OR u.last_name ILIKE ${idx}
                OR r.id::text ILIKE ${idx}
                OR r.rfq_id ILIKE ${idx}
            )
        """
        search_proposal = f"""
            AND (
                p.message ILIKE ${idx}
                OR r.details ILIKE ${idx}
                OR u.email ILIKE ${idx}
                OR u.first_name ILIKE ${idx}
                OR u.last_name ILIKE ${idx}
                OR r.id::text ILIKE ${idx}
                OR r.proposal_id::text ILIKE ${idx}
            )
        """

    sql = f"""
        SELECT id, target_type, reason, details, status, created_at, reporter_user_id, object_id
        FROM (
            SELECT
                r.id, 'catalog'::text AS target_type, r.reason::text AS reason,
                r.details, r.status::text AS status, r.created_at,
                r.reporter_user_id, r.item_id::text AS object_id
            FROM catalog_item_reports r
            JOIN catalog_items ci ON ci.id = r.item_id
            JOIN users u ON u.id = r.reporter_user_id
            WHERE r.status::text = 'open'{reason_sql}{search_catalog}
            UNION ALL
            SELECT
                r.id, 'rfq'::text AS target_type, r.reason::text AS reason,
                r.details, r.status::text AS status, r.created_at,
                r.reporter_user_id, r.rfq_id AS object_id
            FROM rfq_reports r
            JOIN rfqs rfq ON rfq.id = r.rfq_id
            JOIN users u ON u.id = r.reporter_user_id
            WHERE r.status::text = 'open'{reason_sql}{search_rfq}
            UNION ALL
            SELECT
                r.id, 'proposal'::text AS target_type, r.reason::text AS reason,
                r.details, r.status::text AS status, r.created_at,
                r.reporter_user_id, r.proposal_id::text AS object_id
            FROM proposal_reports r
            JOIN proposals p ON p.id = r.proposal_id
            JOIN users u ON u.id = r.reporter_user_id
            WHERE r.status::text = 'open'{reason_sql}{search_proposal}
        ) reports
    """
    return sql, params


def _report_open_status(_target_type: str) -> str:
    return "open"


def _report_dismissed_status(_target_type: str) -> str:
    return "dismissed"


def _report_resolved_status(_target_type: str) -> str:
    return "resolved"


def _object_resource_type(target_type: str) -> str:
    if target_type == "catalog":
        return "catalog_item"
    if target_type == "rfq":
        return "rfq"
    return "proposal"


def _admin_href(target_type: str, object_id) -> str:
    if target_type == "catalog":
        return f"/admin/catalog/{object_id}"
    if target_type == "rfq":
        return f"/admin/rfqs/{object_id}"
    return f"/admin/proposals/{object_id}"


def _owner_href(target_type: str, object_id) -> str:
    if target_type == "catalog":
        return f"/supplier/catalog/{object_id}"
    if target_type == "rfq":
        return f"/customer/rfqs/{object_id}"
    return f"/supplier/proposals/{object_id}"


def _report_action_notification(action: str, object_title: str, reason: str | None):
    messages = {
        "warn": (
            "Предупреждение модерации",
            f"По объекту «{object_title}» вынесено предупреждение.",
        ),
        "suspend": (
            "Аккаунт приостановлен",
            f"Из-за жалобы на «{object_title}» доступ приостановлен.",
        ),
        "delete": (
            "Объект удалён модерацией",
            f"Объект «{object_title}» удалён по результатам жалобы.",
        ),
    }
    title, body = messages[action]
    return title, with_reason(body, reason)


def _report_status_type(target_type: str) -> str:
    if target_type == "catalog":
        return "catalog_item_report_status"
    if target_type == "rfq":
        return "rfq_report_status"
    return "proposal_report_status"


def _report_table(target_type: str) -> str:
    if target_type == "catalog":
        return "catalog_item_reports"
    if target_type == "rfq":
        return "rfq_reports"
    return "proposal_reports"


class AdminReportsMixin:
    async def list_reports(
        self,
        page: int = 1,
        page_size: int = 20,
        view: str = "all",
        query: str | None = None,
    ) -> dict:
        if view not in ("all", *REPORT_REASONS):
            raise ValidationError("Unsupported report view")

        union_sql, params = _reports_union_sql(view, query)
        async with self.connection() as conn:
            total = int(await conn.fetchval(f"SELECT COUNT(*) FROM ({union_sql}) t", *params))
            pages = max(1, (total + page_size - 1) // page_size) if total else 1
            page = min(max(1, page), pages)
            rows = await conn.fetch(
                f"""
                {union_sql}
                ORDER BY created_at DESC, id DESC
                LIMIT $%s OFFSET $%s
                """
                % (len(params) + 1, len(params) + 2),
                *params,
                page_size,
                (page - 1) * page_size,
            )
            items = []
            for row in rows:
                items.append(await self._serialize_report_list_item(conn, row))

            view_counts = {"all": 0}
            for reason in REPORT_REASONS:
                reason_sql, reason_params = _reports_union_sql(reason, query)
                count = int(
                    await conn.fetchval(f"SELECT COUNT(*) FROM ({reason_sql}) t", *reason_params)
                )
                view_counts[reason] = count
                view_counts["all"] += count

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages,
            "view_counts": view_counts,
        }

    async def _serialize_report_list_item(self, conn, row) -> dict:
        reporter = await conn.fetchrow(
            "SELECT id, email, first_name, last_name FROM users WHERE id = $1",
            row["reporter_user_id"],
        )
        target_type = row["target_type"]
        object_id = row["object_id"]
        title = "—"
        href = "/"
        if target_type == "catalog":
            item = await conn.fetchrow(
                "SELECT id, title FROM catalog_items WHERE id = $1",
                int(object_id),
            )
            if item:
                title = item["title"]
                href = f"/admin/catalog/{item['id']}"
        elif target_type == "rfq":
            rfq = await conn.fetchrow("SELECT id, title FROM rfqs WHERE id = $1", object_id)
            if rfq:
                title = rfq["title"]
                href = f"/admin/rfqs/{rfq['id']}"
        elif target_type == "proposal":
            proposal = await conn.fetchrow(
                "SELECT id, message FROM proposals WHERE id = $1",
                int(object_id),
            )
            if proposal:
                title = (proposal["message"] or f"Предложение #{proposal['id']}")[:120]
                href = f"/admin/proposals/{proposal['id']}"

        details = row["details"] or ""
        return {
            "id": row["id"],
            "target_type": target_type,
            "reason": row["reason"],
            "status": row["status"],
            "details_preview": details[:160] if details else None,
            "created_at": row["created_at"],
            "reporter": {
                "id": reporter["id"] if reporter else 0,
                "email": reporter["email"] if reporter else "",
                "name": (
                    f"{reporter['first_name']} {reporter['last_name']}".strip()
                    if reporter
                    else "—"
                ),
            },
            "reported_object": {
                "type": target_type,
                "id": object_id,
                "title": title,
                "href": href,
            },
        }

    async def _load_report_bundle(self, conn, target_type: str, report_id: int):
        if target_type not in REPORT_TARGET_TYPES:
            raise ValidationError("Unsupported report target type")
        if target_type == "catalog":
            report = await conn.fetchrow(
                """
                SELECT r.id, r.item_id, r.reason::text AS reason, r.details,
                       r.status::text AS status, r.created_at, r.resolved_at,
                       r.reporter_user_id,
                       u.email, u.first_name, u.last_name
                FROM catalog_item_reports r
                JOIN users u ON u.id = r.reporter_user_id
                WHERE r.id = $1
                """,
                report_id,
            )
            if not report:
                raise NotFoundError("Report not found")
            target = await conn.fetchrow(
                """
                SELECT id, title, actor_id, status::text AS status
                FROM catalog_items WHERE id = $1
                """,
                report["item_id"],
            )
            if not target:
                raise NotFoundError("Report not found")
            media = await conn.fetch(
                """
                SELECT file_name, file_url, media_type::text AS media_type, sort_order
                FROM item_media WHERE item_id = $1 ORDER BY sort_order
                """,
                target["id"],
            )
            return dict(report), {**dict(target), "media": [dict(row) for row in media]}
        if target_type == "rfq":
            report = await conn.fetchrow(
                """
                SELECT r.id, r.rfq_id, r.reason::text AS reason, r.details,
                       r.status::text AS status, r.created_at, r.resolved_at,
                       r.reporter_user_id,
                       u.email, u.first_name, u.last_name
                FROM rfq_reports r
                JOIN users u ON u.id = r.reporter_user_id
                WHERE r.id = $1
                """,
                report_id,
            )
            if not report:
                raise NotFoundError("Report not found")
            target = await conn.fetchrow(
                "SELECT id, title, actor_id, status::text AS status FROM rfqs WHERE id = $1",
                report["rfq_id"],
            )
            if not target:
                raise NotFoundError("Report not found")
            attachments = await conn.fetch(
                "SELECT file_name, file_url, file_type FROM rfq_attachments WHERE rfq_id = $1",
                target["id"],
            )
            return dict(report), {**dict(target), "attachments": [dict(row) for row in attachments]}

        report = await conn.fetchrow(
            """
            SELECT r.id, r.proposal_id, r.reason::text AS reason, r.details,
                   r.status::text AS status, r.created_at, r.resolved_at,
                   r.reporter_user_id,
                   u.email, u.first_name, u.last_name
            FROM proposal_reports r
            JOIN users u ON u.id = r.reporter_user_id
            WHERE r.id = $1
            """,
            report_id,
        )
        if not report:
            raise NotFoundError("Report not found")
        target = await conn.fetchrow(
            """
            SELECT p.id, p.message, p.supplier_actor_id, p.status::text AS status,
                   pa.file_name, pa.file_url, pa.file_type
            FROM proposals p
            LEFT JOIN proposal_attachments pa ON pa.proposal_id = p.id
            WHERE p.id = $1
            """,
            report["proposal_id"],
        )
        if not target:
            raise NotFoundError("Report not found")
        attachment = None
        if target["file_name"]:
            attachment = {
                "file_name": target["file_name"],
                "file_url": target["file_url"],
                "file_type": target["file_type"],
            }
        return dict(report), {**dict(target), "attachment": attachment}

    async def _resolve_reported_object_owner(self, conn, target_type: str, target) -> dict | None:
        if target_type in {"catalog", "rfq"}:
            owners = await load_owners(conn, [target["actor_id"]])
            return owners.get(target["actor_id"])
        owners = await load_owners(conn, [target["supplier_actor_id"]])
        return owners.get(target["supplier_actor_id"])

    def _reported_object_id(self, target_type: str, target):
        return target["id"]

    def _reported_object_title(self, target_type: str, target) -> str:
        if target_type in {"catalog", "rfq"}:
            return target["title"]
        return (target["message"] or f"Предложение #{target['id']}")[:200]

    def _serialize_reported_object(self, target_type: str, target, owner) -> dict:
        object_id = self._reported_object_id(target_type, target)
        return {
            "type": target_type,
            "id": str(object_id),
            "title": self._reported_object_title(target_type, target),
            "href": _admin_href(target_type, object_id),
            "status": target.get("status"),
            "owner": owner,
        }

    async def _load_report_history(self, conn, target_type: str, target, report_id: int) -> list[dict]:
        object_id = str(self._reported_object_id(target_type, target))
        resource_type = _object_resource_type(target_type)
        rows = await conn.fetch(
            """
            SELECT a.id, a.action, a.details, a.created_at,
                   u.id AS user_id, u.email, u.first_name, u.last_name
            FROM audit_logs a
            LEFT JOIN users u ON u.id = a.user_id
            WHERE (
                (a.resource_type = $1 AND a.resource_id = $2)
                OR (a.resource_type = 'report' AND a.resource_id = $3)
            )
            ORDER BY a.created_at DESC, a.id DESC
            """,
            resource_type,
            object_id,
            f"{target_type}:{report_id}",
        )
        return [serialize_audit_entry(row) for row in rows]

    def _build_report_evidence(self, target_type: str, report, target) -> dict:
        files: list[dict] = []
        if target_type == "catalog":
            for media in target.get("media") or []:
                files.append(
                    {
                        "file_name": media["file_name"],
                        "file_url": file_url(media["file_url"]),
                        "file_type": media["media_type"],
                    }
                )
        elif target_type == "rfq":
            for attachment in target.get("attachments") or []:
                files.append(
                    {
                        "file_name": attachment["file_name"],
                        "file_url": file_url(attachment["file_url"]),
                        "file_type": attachment["file_type"],
                    }
                )
        elif target_type == "proposal" and target.get("attachment"):
            attachment = target["attachment"]
            files.append(
                {
                    "file_name": attachment["file_name"],
                    "file_url": file_url(attachment["file_url"]),
                    "file_type": attachment["file_type"],
                }
            )
        return {"details": report["details"], "files": files}

    async def get_report_detail(self, target_type: str, report_id: int) -> dict:
        async with self.connection() as conn:
            report, target = await self._load_report_bundle(conn, target_type, report_id)
            owner = await self._resolve_reported_object_owner(conn, target_type, target)
            history = await self._load_report_history(conn, target_type, target, report["id"])
        return {
            "id": report["id"],
            "target_type": target_type,
            "reason": report["reason"],
            "status": report["status"],
            "details": report["details"],
            "created_at": report["created_at"],
            "resolved_at": report["resolved_at"],
            "reporter": {
                "id": report["reporter_user_id"],
                "email": report["email"],
                "name": f"{report['first_name']} {report['last_name']}".strip(),
            },
            "reported_object": self._serialize_reported_object(target_type, target, owner),
            "owner": owner,
            "evidence": self._build_report_evidence(target_type, report, target),
            "history": history,
        }

    async def apply_report_action(
        self,
        target_type: str,
        report_id: int,
        action: str,
        current_user,
        reason: str | None = None,
    ) -> dict:
        allowed = {"dismiss", "warn", "suspend", "delete"}
        if action not in allowed:
            raise ValidationError("Unsupported report action")
        if action in {"suspend", "delete"} and actor_role_of(current_user) == "moderator":
            raise ForbiddenError("Moderator cannot perform this action")
        if action in {"warn", "suspend", "delete"} and not (reason and reason.strip()):
            raise ValidationError("Reason is required for this action")

        async with self.connection() as conn:
            report, target = await self._load_report_bundle(conn, target_type, report_id)
            if report["status"] != _report_open_status(target_type):
                raise ConflictError("Report is already closed")
            owner = await self._resolve_reported_object_owner(conn, target_type, target)
            object_title = self._reported_object_title(target_type, target)
            object_id = self._reported_object_id(target_type, target)
            table = _report_table(target_type)
            status_type = _report_status_type(target_type)

            if action == "dismiss":
                status = _report_dismissed_status(target_type)
            else:
                status = _report_resolved_status(target_type)

            if action == "delete":
                await log_admin(
                    conn,
                    actor_id_of(current_user),
                    "admin.report.delete",
                    "report",
                    f"{target_type}:{report['id']}",
                    {
                        "reason": reason.strip() if reason else None,
                        "target_type": target_type,
                        "report_id": report["id"],
                        "object_id": object_id,
                        "report_reason": report["reason"],
                        "status": "resolved",
                    },
                )
                await log_admin(
                    conn,
                    actor_id_of(current_user),
                    "admin.report.delete",
                    _object_resource_type(target_type),
                    str(object_id),
                    {
                        "reason": reason.strip() if reason else None,
                        "report_id": report["id"],
                        "target_type": target_type,
                    },
                )
                owner_user_id = owner.get("user_id") if owner else None
                if owner_user_id:
                    title, body = _report_action_notification(action, object_title, reason)
                    await notify_user(
                        conn,
                        owner_user_id,
                        title,
                        body,
                        href=_owner_href(target_type, object_id),
                    )
                await conn.execute(
                    f"""
                    UPDATE {table}
                    SET status = $1::{status_type}, resolved_at = NOW(), resolved_by_id = $2
                    WHERE id = $3
                    """,
                    status,
                    actor_id_of(current_user),
                    report_id,
                )

        if action == "delete":
            await self._delete_reported_object(target_type, target, current_user, reason)
            return {
                "id": report_id,
                "target_type": target_type,
                "action": action,
                "status": "resolved",
            }

        async with self.connection() as conn:
            await conn.execute(
                f"""
                UPDATE {table}
                SET status = $1::{status_type}, resolved_at = NOW(), resolved_by_id = $2
                WHERE id = $3
                """,
                status,
                actor_id_of(current_user),
                report_id,
            )
            if action == "suspend":
                await self._suspend_reported_owner(owner, current_user, reason)
            if action in {"warn", "suspend"}:
                owner_user_id = owner.get("user_id") if owner else None
                if owner_user_id:
                    title, body = _report_action_notification(action, object_title, reason)
                    await notify_user(
                        conn,
                        owner_user_id,
                        title,
                        body,
                        href=_owner_href(target_type, object_id),
                    )
            await log_admin(
                conn,
                actor_id_of(current_user),
                f"admin.report.{action}",
                "report",
                f"{target_type}:{report['id']}",
                {
                    "reason": reason.strip() if reason else None,
                    "target_type": target_type,
                    "report_id": report["id"],
                    "object_id": object_id,
                    "report_reason": report["reason"],
                    "status": status,
                },
            )
            await log_admin(
                conn,
                actor_id_of(current_user),
                f"admin.report.{action}",
                _object_resource_type(target_type),
                str(object_id),
                {
                    "reason": reason.strip() if reason else None,
                    "report_id": report["id"],
                    "target_type": target_type,
                },
            )
        return {
            "id": report["id"],
            "target_type": target_type,
            "action": action,
            "status": status,
        }

    async def _suspend_reported_owner(self, owner, current_user, reason: str | None) -> None:
        if not owner:
            raise ValidationError("Reported object has no owner to suspend")
        company_id = owner.get("company_id")
        user_id = owner.get("user_id")
        if company_id:
            await self.apply_company_action(
                company_id=company_id,
                action="block",
                current_user=current_user,
                reason=reason,
            )
            return
        if not user_id:
            raise ValidationError("Reported object has no owner to suspend")
        await self.update_user_status(
            user_id=user_id,
            status="blocked",
            current_user=current_user,
        )

    async def _delete_reported_object(self, target_type: str, target, current_user, reason: str | None) -> None:
        if target_type == "catalog":
            await self.apply_catalog_action(
                item_id=target["id"],
                action="delete",
                current_user=current_user,
                reason=reason,
            )
            return
        if target_type == "rfq":
            await self.apply_rfq_action(
                rfq_id=target["id"],
                action="delete",
                current_user=current_user,
                reason=reason,
            )
            return
        await self.apply_proposal_action(
            proposal_id=target["id"],
            action="delete",
            current_user=current_user,
            reason=reason,
        )
