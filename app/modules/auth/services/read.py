from app.core.permissions import STAFF_PLATFORM_ROLES, fetch_permission_names
from app.modules.actors.services import ActorService, user_capabilities
from app.modules.auth.schemas import CompanySummary, MeResponse, StaffRoleSummary
from app.modules.auth.services.common import actor_to_summary, load_user, shape_user_public


class AuthReadMixin:
    async def get_me(self, user_id: int, active_actor_id: int | None = None) -> MeResponse:
        async with self.connection() as conn:
            user = await load_user(conn, user_id)
            if not user:
                from app.core.exceptions import UnauthorizedError

                raise UnauthorizedError("User not found")
            actor_svc = ActorService(conn)
            await actor_svc.ensure_individual_actors_for_user(user)
            membership_rows = await conn.fetch(
                """
                SELECT
                    cu.company_id,
                    cu.role::text AS role,
                    c.id,
                    c.title,
                    c.actor_type::text AS actor_type,
                    c.verification_status::text AS verification_status
                FROM company_users cu
                JOIN companies c ON c.id = cu.company_id
                WHERE cu.user_id = $1
                """,
                user_id,
            )
            actors = await actor_svc.list_for_user(user_id)
            permissions = await fetch_permission_names(
                conn, role_id=user.get("role_id"), role_name=user["role"]
            )
            staff_role = None
            if user["role"] in STAFF_PLATFORM_ROLES and user.get("role_id"):
                role_row = await conn.fetchrow(
                    "SELECT id, name, description FROM roles WHERE id = $1",
                    user["role_id"],
                )
                if role_row:
                    staff_role = StaffRoleSummary(**dict(role_row))

        memberships = {row["company_id"]: dict(row) for row in membership_rows}
        companies = [
            CompanySummary(
                id=row["id"],
                title=row["title"],
                actor_type=row["actor_type"],
                verification_status=row["verification_status"],
                role=row["role"],
            )
            for row in memberships.values()
        ]
        actor_summaries = [actor_to_summary(actor, memberships) for actor in actors]
        valid_ids = {a.id for a in actor_summaries}
        if active_actor_id is not None and active_actor_id not in valid_ids:
            active_actor_id = None
        if active_actor_id is None and actor_summaries:
            individual = next((a for a in actor_summaries if a.kind == "individual"), None)
            active_actor_id = individual.id if individual else actor_summaries[0].id
        active_company_id = None
        if active_actor_id:
            active = next((a for a in actor_summaries if a.id == active_actor_id), None)
            if active and active.company_id:
                active_company_id = active.company_id
        return MeResponse(
            user=shape_user_public(user),
            actors=actor_summaries,
            active_actor_id=active_actor_id,
            capabilities=user_capabilities(user["role"]),
            companies=companies,
            active_company_id=active_company_id,
            permissions=sorted(permissions),
            staff_role=staff_role,
        )
