from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from src.models import (
    Actor,
    ActorKind,
    ActorType,
    CatalogItem,
    Category,
    Company,
    CompanyCategory,
    CompanyCertificate,
    CompanyProfile,
    CompanyRole,
    CompanyStats,
    CompanyUser,
    ItemStatus,
    Review,
    User,
    UserRole,
    VerificationStatus,
)
from src.modules.actors.service import ActorService
from src.modules.companies.schemas import (
    AddTeamMemberRequest,
    CertificateCreateRequest,
    CompanyUpdateRequest,
    CompanyWizardInput,
    CompanyWithRelations,
    PublicSupplier,
)
from src.shared.serializers import company_to_schema


class CompanyService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _load_company(self, company_id: int) -> Company:
        result = await self.db.execute(
            select(Company)
            .where(Company.id == company_id)
            .options(
                selectinload(Company.profile),
                selectinload(Company.stats),
                selectinload(Company.categories),
                selectinload(Company.certificates),
                selectinload(Company.actors),
                selectinload(Company.members).selectinload(CompanyUser.user),
            )
        )
        company = result.scalar_one_or_none()
        if not company:
            raise NotFoundError("Company not found")
        return company

    def _parse_actor_types(self, data: CompanyWizardInput) -> list[ActorType]:
        if data.actor_types:
            return [ActorType(t) for t in data.actor_types]
        if data.actor_type:
            return [ActorType(data.actor_type)]
        return [ActorType.buyer]

    async def _ensure_roles_and_actors(self, user: User, actor_types: list[ActorType]) -> None:
        for side in actor_types:
            if side == ActorType.buyer and user.role == UserRole.supplier:
                user.role = UserRole.both
            elif side == ActorType.supplier and user.role == UserRole.buyer:
                user.role = UserRole.both
            elif side == ActorType.buyer and user.role not in (UserRole.buyer, UserRole.both):
                user.role = UserRole.buyer
            elif side == ActorType.supplier and user.role not in (UserRole.supplier, UserRole.both):
                user.role = UserRole.supplier
        user.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        actor_svc = ActorService(self.db)
        for side in actor_types:
            await actor_svc.ensure_individual_actor(user, side)

    async def create_from_wizard(
        self, user: User, data: CompanyWizardInput, actor_type: ActorType | None = None
    ) -> CompanyWithRelations:
        actor_types = self._parse_actor_types(data)
        if actor_type and actor_type not in actor_types:
            actor_types = [actor_type, *actor_types]

        await self._ensure_roles_and_actors(user, actor_types)

        primary_type = actor_types[0]
        company = Company(
            title=data.title,
            actor_type=primary_type,
            owner_id=user.id,
            legal_name=data.legal_name or None,
            tax_number=data.tax_number or None,
            website=data.website or None,
            description=data.description or None,
            logo=data.logo or None,
            country=data.country or None,
            city=data.city or None,
            address=data.address or None,
            verification_status=VerificationStatus.pending,
            rating=0.0,
        )
        self.db.add(company)
        await self.db.flush()

        founded = int(data.founded_year) if data.founded_year.isdigit() else None
        employees = int(data.employees_count) if data.employees_count.isdigit() else None
        self.db.add(
            CompanyProfile(
                company_id=company.id,
                founded_year=founded,
                employees_count=employees,
                annual_revenue_range=data.annual_revenue_range or None,
                languages=data.languages,
                industries=data.industries,
            )
        )
        self.db.add(
            CompanyStats(
                company_id=company.id,
                completed_contracts=0,
                active_contracts=0,
                disputes_count=0,
                average_rating=0.0,
            )
        )
        self.db.add(
            CompanyUser(company_id=company.id, user_id=user.id, role=CompanyRole.director)
        )
        for cat_id in data.category_ids:
            self.db.add(CompanyCategory(company_id=company.id, category_id=cat_id))
        for cert in data.certificates:
            self.db.add(
                CompanyCertificate(
                    company_id=company.id,
                    title=cert.title,
                    issuer=cert.issuer,
                    issue_date=cert.issue_date,
                    expiry_date=cert.expiry_date or None,
                    file_url=cert.file_url,
                )
            )
        for member in data.team:
            result = await self.db.execute(select(User).where(User.email == member.email.lower()))
            member_user = result.scalar_one_or_none()
            if member_user:
                self.db.add(
                    CompanyUser(
                        company_id=company.id,
                        user_id=member_user.id,
                        role=CompanyRole(member.role),
                    )
                )

        actor_svc = ActorService(self.db)
        await actor_svc.sync_company_actors(company, actor_types)
        await self.db.flush()
        return company_to_schema(await self._load_company(company.id))

    async def get_my_companies(self, user_id: int) -> list[CompanyWithRelations]:
        result = await self.db.execute(
            select(Company)
            .join(CompanyUser)
            .where(CompanyUser.user_id == user_id)
            .options(
                selectinload(Company.profile),
                selectinload(Company.stats),
                selectinload(Company.categories),
                selectinload(Company.certificates),
                selectinload(Company.actors),
                selectinload(Company.members).selectinload(CompanyUser.user),
            )
        )
        return [company_to_schema(c) for c in result.scalars().all()]

    async def get_company(self, company_id: int) -> CompanyWithRelations:
        return company_to_schema(await self._load_company(company_id))

    async def update_company(
        self, company_id: int, user_id: int, data: CompanyUpdateRequest
    ) -> CompanyWithRelations:
        company = await self._load_company(company_id)
        await self._ensure_can_manage(company_id, user_id)
        for field in (
            "title",
            "legal_name",
            "tax_number",
            "website",
            "description",
            "logo",
            "country",
            "city",
            "address",
        ):
            val = getattr(data, field)
            if val is not None:
                setattr(company, field, val)
        if company.profile:
            if data.founded_year is not None:
                company.profile.founded_year = data.founded_year
            if data.employees_count is not None:
                company.profile.employees_count = data.employees_count
            if data.annual_revenue_range is not None:
                company.profile.annual_revenue_range = data.annual_revenue_range
            if data.languages is not None:
                company.profile.languages = data.languages
            if data.industries is not None:
                company.profile.industries = data.industries
        if data.category_ids is not None:
            for link in list(company.categories):
                await self.db.delete(link)
            for cat_id in data.category_ids:
                self.db.add(CompanyCategory(company_id=company.id, category_id=cat_id))

        if data.actor_types is not None:
            actor_types = [ActorType(t) for t in data.actor_types]
            if actor_types:
                company.actor_type = actor_types[0]
            actor_svc = ActorService(self.db)
            await actor_svc.sync_company_actors(company, actor_types)

        company.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        return company_to_schema(await self._load_company(company_id))

    async def list_suppliers(
        self, query: str | None = None, category_slug: str | None = None
    ) -> list[PublicSupplier]:
        stmt = (
            select(Actor)
            .where(
                Actor.side == ActorType.supplier,
                Actor.is_active.is_(True),
            )
            .options(
                selectinload(Actor.company).selectinload(Company.profile),
                selectinload(Actor.company).selectinload(Company.stats),
                selectinload(Actor.company).selectinload(Company.categories),
                selectinload(Actor.user),
            )
            .order_by(Actor.display_name.asc())
        )
        if query:
            pattern = f"%{query}%"
            stmt = (
                stmt.outerjoin(Company, Actor.company_id == Company.id)
                .outerjoin(User, Actor.user_id == User.id)
                .where(
                    or_(
                        Actor.display_name.ilike(pattern),
                        Company.title.ilike(pattern),
                        Company.description.ilike(pattern),
                        User.first_name.ilike(pattern),
                        User.last_name.ilike(pattern),
                    )
                )
            )
        if category_slug:
            stmt = (
                stmt.join(Company, Actor.company_id == Company.id)
                .join(CompanyCategory, CompanyCategory.company_id == Company.id)
                .join(Category, Category.id == CompanyCategory.category_id)
                .where(
                    Actor.kind == ActorKind.company,
                    Category.slug == category_slug,
                )
            )
        result = await self.db.execute(stmt)
        actors = result.scalars().unique().all()
        return [await self._actor_to_public_supplier(actor) for actor in actors]

    async def get_public_supplier(self, actor_id: int) -> PublicSupplier:
        result = await self.db.execute(
            select(Actor)
            .where(
                Actor.id == actor_id,
                Actor.side == ActorType.supplier,
                Actor.is_active.is_(True),
            )
            .options(
                selectinload(Actor.company).selectinload(Company.profile),
                selectinload(Actor.company).selectinload(Company.stats),
                selectinload(Actor.company).selectinload(Company.categories),
                selectinload(Actor.user),
            )
        )
        actor = result.scalar_one_or_none()
        if not actor:
            raise NotFoundError("Supplier not found")
        return await self._actor_to_public_supplier(actor)

    async def _actor_to_public_supplier(self, actor: Actor) -> PublicSupplier:
        reviews_count_result = await self.db.execute(
            select(func.count(Review.id)).where(Review.target_actor_id == actor.id)
        )
        reviews_count = int(reviews_count_result.scalar() or 0)

        catalog_count_result = await self.db.execute(
            select(func.count(CatalogItem.id)).where(
                CatalogItem.actor_id == actor.id,
                CatalogItem.status == ItemStatus.active,
            )
        )
        active_catalog_count = int(catalog_count_result.scalar() or 0)

        company = actor.company
        if actor.kind == ActorKind.company and company:
            rating = float(company.rating or 0)
            if company.stats and company.stats.average_rating:
                rating = float(company.stats.average_rating)
            industries = list(company.profile.industries) if company.profile else []
            return PublicSupplier(
                actor_id=actor.id,
                kind=actor.kind.value,
                display_name=actor.display_name or company.title,
                company_id=company.id,
                city=company.city,
                country=company.country,
                description=company.description,
                website=company.website,
                rating=rating,
                verification_status=company.verification_status.value,
                reviews_count=reviews_count,
                industries=industries,
                active_catalog_count=active_catalog_count,
                trust_level=actor.trust_level.value,
            )

        user = actor.user
        description = None
        if user:
            description = f"{user.first_name} {user.last_name}".strip() or None
        return PublicSupplier(
            actor_id=actor.id,
            kind=actor.kind.value,
            display_name=actor.display_name
            or (description if description else f"Исполнитель #{actor.id}"),
            company_id=None,
            city=None,
            country=None,
            description=None,
            website=None,
            rating=0.0,
            verification_status="pending",
            reviews_count=reviews_count,
            industries=[],
            active_catalog_count=active_catalog_count,
            trust_level=actor.trust_level.value,
        )

    async def add_certificate(
        self, company_id: int, user_id: int, data: CertificateCreateRequest
    ):
        await self._ensure_can_manage(company_id, user_id)
        cert = CompanyCertificate(
            company_id=company_id,
            title=data.title,
            issuer=data.issuer,
            issue_date=data.issue_date,
            expiry_date=data.expiry_date,
            file_url=data.file_url,
        )
        self.db.add(cert)
        await self.db.flush()
        return cert

    async def add_team_member(self, company_id: int, user_id: int, data: AddTeamMemberRequest):
        await self._ensure_can_manage(company_id, user_id)
        result = await self.db.execute(select(User).where(User.email == data.email.lower()))
        member = result.scalar_one_or_none()
        if not member:
            raise NotFoundError("User with this email not found")
        existing = await self.db.execute(
            select(CompanyUser).where(
                CompanyUser.company_id == company_id, CompanyUser.user_id == member.id
            )
        )
        if existing.scalar_one_or_none():
            raise ConflictError("User already in team")
        cu = CompanyUser(
            company_id=company_id, user_id=member.id, role=CompanyRole(data.role)
        )
        self.db.add(cu)
        await self.db.flush()
        return cu

    async def remove_team_member(self, company_id: int, user_id: int, target_user_id: int):
        await self._ensure_can_manage(company_id, user_id)
        result = await self.db.execute(
            select(CompanyUser).where(
                CompanyUser.company_id == company_id, CompanyUser.user_id == target_user_id
            )
        )
        membership = result.scalar_one_or_none()
        if not membership:
            raise NotFoundError("Team member not found")
        if membership.role == CompanyRole.director:
            raise ForbiddenError("Cannot remove company director")
        await self.db.delete(membership)

    async def _ensure_can_manage(self, company_id: int, user_id: int) -> None:
        result = await self.db.execute(
            select(CompanyUser).where(
                CompanyUser.company_id == company_id, CompanyUser.user_id == user_id
            )
        )
        membership = result.scalar_one_or_none()
        if not membership or membership.role not in (CompanyRole.director, CompanyRole.admin):
            raise ForbiddenError("Insufficient permissions")
