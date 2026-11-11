from app.modules.notifications.schemas import NotificationSchema
from app.modules.notifications.services.common import (
    NOTIFICATION_COLUMNS,
    notification_type_value,
    shape_notification_row,
)
from app.modules.notifications.ws_manager import notification_ws_manager
from app.utils.side_roles import side_access_sql


class NotificationCreateMixin:
    async def resolve_actor_recipients(self, actor_id: int) -> list[tuple[int, int | None]]:
        async with self.connection() as conn:
            actor = await conn.fetchrow(
                """
                SELECT kind::text AS kind, side::text AS side, user_id, company_id
                FROM actors
                WHERE id = $1
                """,
                actor_id,
            )
            if not actor:
                return []
            if actor["kind"] == "individual":
                if actor["user_id"] is None:
                    return []
                return [(actor["user_id"], None)]
            if actor["company_id"] is None:
                return []
            # Members who closed this side (purchases or sales) do not get its news.
            members = await conn.fetch(
                f"""
                SELECT cu.user_id FROM company_users cu
                WHERE cu.company_id = $1 AND {side_access_sql("cu", "$2")}
                """,
                actor["company_id"],
                actor["side"],
            )
            return [(row["user_id"], actor["company_id"]) for row in members]

    async def _insert_notification(
        self,
        conn,
        user_id: int,
        company_id: int | None,
        ntype: str,
        title: str,
        body: str,
        href: str | None,
    ) -> NotificationSchema:
        row = await conn.fetchrow(
            f"""
            INSERT INTO notifications (user_id, company_id, type, title, body, href, read, created_at)
            VALUES ($1, $2, $3::notification_type, $4, $5, $6, FALSE, NOW())
            RETURNING {NOTIFICATION_COLUMNS}
            """,
            user_id,
            company_id,
            ntype,
            title,
            body,
            href,
        )
        return shape_notification_row(row)

    async def _push_created(self, user_id: int, notification: NotificationSchema) -> None:
        # The same words go to the person's Telegram, if they linked it to the bot.
        from app.modules.telegram.assistant import schedule_notification

        schedule_notification(user_id, notification.title, notification.body, notification.href)
        await notification_ws_manager.broadcast_to_users(
            [user_id],
            {
                "event": "notification.created",
                "data": notification.model_dump(mode="json"),
            },
        )

    async def notify_actor_members(
        self,
        actor_id: int,
        ntype,
        title: str,
        body: str,
        href: str | None = None,
    ) -> list[NotificationSchema]:
        recipients = await self.resolve_actor_recipients(actor_id)
        if not recipients:
            return []
        type_value = notification_type_value(ntype)
        created: list[NotificationSchema] = []
        async with self.connection() as conn:
            async with conn.transaction():
                for user_id, company_id in recipients:
                    created.append(
                        await self._insert_notification(
                            conn, user_id, company_id, type_value, title, body, href
                        )
                    )
        for (user_id, _company_id), notification in zip(recipients, created):
            await self._push_created(user_id, notification)
        return created

    async def notify_rfq_owner(self, rfq, title: str, body: str, href: str | None = None):
        return await self.notify_actor_members(
            rfq.actor_id,
            "proposal",
            title,
            body,
            href or f"/customer/rfqs/{rfq.id}/proposals",
        )

    async def notify_supplier_on_accept(self, proposal, contract_id: int):
        rfq = proposal.rfq
        return await self.notify_actor_members(
            proposal.supplier_actor_id,
            "contract",
            "Предложение принято",
            f"Заказчик принял ваше предложение на RFQ «{rfq.title}»",
            f"/supplier/contracts/{contract_id}",
        )

    async def notify_contract_closed(self, contract) -> list[NotificationSchema]:
        contract_id = contract["id"]
        title = "Проект закрыт"
        body = f"Договор «{contract['title']}» завершён"
        created: list[NotificationSchema] = []
        created.extend(
            await self.notify_actor_members(
                contract["buyer_actor_id"],
                "contract",
                title,
                body,
                f"/customer/contracts/{contract_id}",
            )
        )
        created.extend(
            await self.notify_actor_members(
                contract["supplier_actor_id"],
                "contract",
                title,
                body,
                f"/supplier/contracts/{contract_id}",
            )
        )
        return created

    async def notify_work_submitted(self, contract) -> list[NotificationSchema]:
        contract_id = contract["id"]
        return await self.notify_actor_members(
            contract["buyer_actor_id"],
            "contract",
            "Работа сдана",
            f"Исполнитель сдал работу по договору «{contract['title']}»",
            f"/customer/contracts/{contract_id}",
        )

    async def notify_submission_rejected(self, contract) -> list[NotificationSchema]:
        contract_id = contract["id"]
        return await self.notify_actor_members(
            contract["supplier_actor_id"],
            "contract",
            "Нужны правки",
            f"Заказчик запросил правки по договору «{contract['title']}»",
            f"/supplier/contracts/{contract_id}",
        )

    async def notify_user(
        self,
        user_id: int,
        ntype,
        title: str,
        body: str,
        href: str | None = None,
        company_id: int | None = None,
    ) -> NotificationSchema:
        type_value = notification_type_value(ntype)
        async with self.connection() as conn:
            notification = await self._insert_notification(
                conn, user_id, company_id, type_value, title, body, href
            )
        await self._push_created(user_id, notification)
        return notification
