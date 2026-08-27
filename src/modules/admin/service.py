from datetime import datetime, timezone

from sqlalchemy import String, cast, func, literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from src.utils.storage import public_file_url
from src.models import (
    Actor,
    ActorKind,
    AuditLog,
    CatalogItem,
    CatalogItemReport,
    CatalogItemReportReason,
    CatalogItemReportStatus,
    CatalogItemType,
    Company,
    CompanyOperationalStatus,
    CompanyUser,
    Contract,
    ContractStatus,
    Conversation,
    Dispute,
    DisputeResolution,
    DisputeStatus,
    InvoiceStatus,
    ItemStatus,
    Notification,
    NotificationType,
    PaymentMilestone,
    PaymentMilestoneStatus,
    PaymentPlan,
    PlatformPayment,
    PlatformPaymentGateway,
    PlatformPaymentStatus,
    PlatformPaymentType,
    Proposal,
    ProposalReport,
    ProposalReportReason,
    ProposalReportStatus,
    ProposalStatus,
    Review,
    Rfq,
    RfqReport,
    RfqReportReason,
    RfqReportStatus,
    RfqStatus,
    SupplierInvoice,
    Withdrawal,
    WithdrawalStatus,
    User,
    UserRole,
    UserStatus,
    VerificationStatus,
)
from src.modules.auth.schemas import UserPublic
from src.utils.audit import log_audit


class AdminService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_users(
        self,
        page: int = 1,
        page_size: int = 20,
        status: UserStatus | None = None,
        query: str | None = None,
    ) -> dict:
        search_filters = []
        normalized_query = query.strip() if query else ""
        if normalized_query:
            search_pattern = f"%{normalized_query}%"
            search_conditions = [
                User.email.ilike(search_pattern),
                User.first_name.ilike(search_pattern),
                User.last_name.ilike(search_pattern),
                (User.first_name + " " + User.last_name).ilike(search_pattern),
                User.phone.ilike(search_pattern),
                cast(User.id, String).ilike(search_pattern),
            ]
            if normalized_query.isdigit():
                search_conditions.append(User.id == int(normalized_query))
            search_filters.append(or_(*search_conditions))

        filters = list(search_filters)
        if status:
            filters.append(User.status == status)

        total_result = await self.db.execute(
            select(func.count()).select_from(User).where(*filters)
        )
        total = int(total_result.scalar_one())

        counts_result = await self.db.execute(
            select(User.status, func.count())
            .where(*search_filters)
            .group_by(User.status)
        )
        counts = {user_status.value: count for user_status, count in counts_result.all()}

        users_result = await self.db.execute(
            select(User)
            .where(*filters)
            .order_by(User.created_at.desc(), User.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = [UserPublic.model_validate(user) for user in users_result.scalars().all()]

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
            "status_counts": {
                "all": sum(counts.values()),
                "active": counts.get(UserStatus.active.value, 0),
                "blocked": counts.get(UserStatus.blocked.value, 0),
                "pending": counts.get(UserStatus.pending.value, 0),
            },
        }

    async def list_companies(
        self,
        page: int = 1,
        page_size: int = 20,
        status: str = "all",
        query: str | None = None,
    ) -> dict:
        search_filters = []
        normalized_query = query.strip() if query else ""
        if normalized_query:
            search_pattern = f"%{normalized_query}%"
            search_conditions = [
                Company.title.ilike(search_pattern),
                Company.legal_name.ilike(search_pattern),
                Company.tax_number.ilike(search_pattern),
                User.email.ilike(search_pattern),
                User.first_name.ilike(search_pattern),
                User.last_name.ilike(search_pattern),
                cast(Company.id, String).ilike(search_pattern),
            ]
            if normalized_query.isdigit():
                search_conditions.append(Company.id == int(normalized_query))
            search_filters.append(or_(*search_conditions))

        status_filter = self._company_status_filter(status)
        filters = [*search_filters]
        if status_filter is not None:
            filters.append(status_filter)

        base_query = select(Company).join(User, Company.owner_id == User.id)
        total_result = await self.db.execute(
            select(func.count())
            .select_from(Company)
            .join(User, Company.owner_id == User.id)
            .where(*filters)
        )
        total = int(total_result.scalar_one())

        count_values: dict[str, int] = {}
        for count_status in ("all", "verified", "pending", "rejected", "blocked"):
            count_filter = self._company_status_filter(count_status)
            count_filters = [*search_filters]
            if count_filter is not None:
                count_filters.append(count_filter)
            count_result = await self.db.execute(
                select(func.count())
                .select_from(Company)
                .join(User, Company.owner_id == User.id)
                .where(*count_filters)
            )
            count_values[count_status] = int(count_result.scalar_one())

        companies_result = await self.db.execute(
            base_query.where(*filters)
            .options(selectinload(Company.owner), selectinload(Company.actors))
            .order_by(Company.created_at.desc(), Company.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        companies = companies_result.scalars().unique().all()

        return {
            "items": [self._serialize_company_list_item(company) for company in companies],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
            "status_counts": count_values,
        }

    async def get_company_detail(self, company_id: int) -> dict:
        result = await self.db.execute(
            select(Company)
            .where(Company.id == company_id)
            .options(
                selectinload(Company.owner),
                selectinload(Company.profile),
                selectinload(Company.stats),
                selectinload(Company.certificates),
                selectinload(Company.actors),
                selectinload(Company.members).selectinload(CompanyUser.user),
            )
        )
        company = result.scalar_one_or_none()
        if not company:
            raise NotFoundError("Company not found")

        actor_ids = [actor.id for actor in company.actors]
        catalog_items = []
        contracts = []
        reviews = []
        if actor_ids:
            catalog_result = await self.db.execute(
                select(CatalogItem)
                .where(
                    CatalogItem.actor_id.in_(actor_ids),
                    CatalogItem.status != ItemStatus.deleted,
                )
                .options(
                    selectinload(CatalogItem.category),
                    selectinload(CatalogItem.pricing),
                    selectinload(CatalogItem.stats),
                    selectinload(CatalogItem.media),
                )
                .order_by(CatalogItem.created_at.desc())
            )
            catalog_items = catalog_result.scalars().unique().all()

            contracts_result = await self.db.execute(
                select(Contract)
                .where(
                    or_(
                        Contract.buyer_actor_id.in_(actor_ids),
                        Contract.supplier_actor_id.in_(actor_ids),
                    )
                )
                .order_by(Contract.created_at.desc())
                .limit(50)
            )
            contracts = contracts_result.scalars().all()

            reviews_result = await self.db.execute(
                select(Review)
                .where(Review.target_actor_id.in_(actor_ids))
                .order_by(Review.created_at.desc())
                .limit(50)
            )
            reviews = reviews_result.scalars().all()

        return {
            **self._serialize_company_list_item(company),
            "legal_name": company.legal_name,
            "tax_number": company.tax_number,
            "website": company.website,
            "description": company.description,
            "address": company.address,
            "updated_at": company.updated_at,
            "profile": (
                {
                    "founded_year": company.profile.founded_year,
                    "employees_count": company.profile.employees_count,
                    "annual_revenue_range": company.profile.annual_revenue_range,
                    "languages": company.profile.languages,
                    "industries": company.profile.industries,
                }
                if company.profile
                else None
            ),
            "stats": (
                {
                    "completed_contracts": company.stats.completed_contracts,
                    "active_contracts": company.stats.active_contracts,
                    "disputes_count": company.stats.disputes_count,
                    "average_rating": company.stats.average_rating,
                }
                if company.stats
                else None
            ),
            "members": [
                {
                    "id": membership.id,
                    "user_id": membership.user_id,
                    "role": membership.role.value,
                    "email": membership.user.email,
                    "name": (
                        f"{membership.user.first_name} {membership.user.last_name}".strip()
                    ),
                    "status": membership.user.status.value,
                }
                for membership in company.members
            ],
            "certificates": [
                {
                    "id": certificate.id,
                    "title": certificate.title,
                    "issuer": certificate.issuer,
                    "issue_date": certificate.issue_date,
                    "expiry_date": certificate.expiry_date,
                    "file_url": public_file_url(certificate.file_url),
                }
                for certificate in company.certificates
            ],
            "products": [
                self._serialize_catalog_item(item)
                for item in catalog_items
                if item.type == CatalogItemType.product
            ],
            "services": [
                self._serialize_catalog_item(item)
                for item in catalog_items
                if item.type == CatalogItemType.service
            ],
            "contracts": [
                {
                    "id": contract.id,
                    "title": contract.title,
                    "status": contract.status.value,
                    "agreed_amount": contract.agreed_amount,
                    "currency": contract.currency.value,
                    "buyer_actor_id": contract.buyer_actor_id,
                    "supplier_actor_id": contract.supplier_actor_id,
                    "created_at": contract.created_at,
                }
                for contract in contracts
            ],
            "reviews": [
                {
                    "id": review.id,
                    "contract_id": review.contract_id,
                    "reviewer_actor_id": review.reviewer_actor_id,
                    "rating": review.rating,
                    "comment": review.comment,
                    "created_at": review.created_at,
                }
                for review in reviews
            ],
            "verification_checklist": {
                "legal_name": bool(company.legal_name),
                "tax_number": bool(company.tax_number),
                "address": bool(company.address),
                "website": bool(company.website),
                "certificates": bool(company.certificates),
            },
        }

    async def apply_company_action(
        self,
        company_id: int,
        action: str,
        current_user: User,
        reason: str | None = None,
    ) -> dict:
        result = await self.db.execute(
            select(Company)
            .where(Company.id == company_id)
            .options(selectinload(Company.actors))
        )
        company = result.scalar_one_or_none()
        if not company:
            raise NotFoundError("Company not found")

        verification_actions = {"approve", "reject", "request_documents"}
        operational_actions = {"block", "deactivate", "reactivate"}
        if action not in verification_actions | operational_actions:
            raise ValidationError("Unsupported company action")
        if action in operational_actions and current_user.role == UserRole.moderator:
            raise ForbiddenError("Moderator cannot change company operational status")
        if action in {"reject", "request_documents", "block", "deactivate"} and not (
            reason and reason.strip()
        ):
            raise ValidationError("Reason is required for this action")

        previous_verification = company.verification_status.value
        previous_operational = company.operational_status.value

        if action == "approve":
            company.verification_status = VerificationStatus.verified
        elif action == "reject":
            company.verification_status = VerificationStatus.rejected
        elif action == "request_documents":
            company.verification_status = VerificationStatus.needs_documents
        elif action == "block":
            company.operational_status = CompanyOperationalStatus.blocked
            for actor in company.actors:
                actor.is_active = False
        elif action == "deactivate":
            company.operational_status = CompanyOperationalStatus.deactivated
            for actor in company.actors:
                actor.is_active = False
        elif action == "reactivate":
            company.operational_status = CompanyOperationalStatus.active
            for actor in company.actors:
                actor.is_active = True

        notification_title, notification_body = self._company_action_notification(
            action,
            company.title,
            reason,
        )
        self.db.add(
            Notification(
                user_id=company.owner_id,
                company_id=company.id,
                type=NotificationType.system,
                title=notification_title,
                body=notification_body,
                href=f"/{'supplier' if company.actor_type.value == 'supplier' else 'customer'}/company/{company.id}",
            )
        )
        await log_audit(
            self.db,
            user_id=current_user.id,
            action=f"admin.company.{action}",
            resource_type="company",
            resource_id=str(company.id),
            details={
                "reason": reason.strip() if reason else None,
                "previous_verification_status": previous_verification,
                "verification_status": company.verification_status.value,
                "previous_operational_status": previous_operational,
                "operational_status": company.operational_status.value,
            },
        )
        await self.db.flush()

        return {
            "id": company.id,
            "action": action,
            "verification_status": company.verification_status.value,
            "operational_status": company.operational_status.value,
        }

    @staticmethod
    def _company_status_filter(status: str):
        if status == "verified":
            return Company.verification_status == VerificationStatus.verified
        if status == "pending":
            return Company.verification_status.in_(
                [VerificationStatus.pending, VerificationStatus.needs_documents]
            )
        if status == "rejected":
            return Company.verification_status == VerificationStatus.rejected
        if status == "blocked":
            return Company.operational_status == CompanyOperationalStatus.blocked
        return None

    @staticmethod
    def _serialize_company_list_item(company: Company) -> dict:
        return {
            "id": company.id,
            "title": company.title,
            "actor_type": company.actor_type.value,
            "actor_types": sorted({actor.side.value for actor in company.actors}),
            "owner": {
                "id": company.owner.id,
                "email": company.owner.email,
                "name": f"{company.owner.first_name} {company.owner.last_name}".strip(),
            },
            "legal_name": company.legal_name,
            "tax_number": company.tax_number,
            "logo": public_file_url(company.logo) if company.logo else company.logo,
            "country": company.country,
            "city": company.city,
            "verification_status": company.verification_status.value,
            "operational_status": company.operational_status.value,
            "rating": company.rating,
            "created_at": company.created_at,
        }

    @staticmethod
    def _serialize_catalog_item(item: CatalogItem) -> dict:
        price = None
        currency = None
        if item.pricing:
            price = (
                item.pricing.fixed_price
                or item.pricing.hourly_rate
                or item.pricing.monthly_rate
            )
            currency = item.pricing.currency
        return {
            "id": item.id,
            "title": item.title,
            "status": item.status.value,
            "category": item.category.name if item.category else None,
            "price": price,
            "currency": currency,
            "views": item.stats.views if item.stats else 0,
            "leads": item.stats.leads if item.stats else 0,
            "created_at": item.created_at,
        }

    @staticmethod
    def _company_action_notification(
        action: str,
        company_title: str,
        reason: str | None,
    ) -> tuple[str, str]:
        messages = {
            "approve": (
                "Компания верифицирована",
                f"Компания «{company_title}» прошла проверку.",
            ),
            "reject": (
                "Верификация отклонена",
                f"Проверка компании «{company_title}» отклонена.",
            ),
            "request_documents": (
                "Нужны дополнительные документы",
                f"Для компании «{company_title}» запрошены дополнительные документы.",
            ),
            "block": (
                "Компания заблокирована",
                f"Компания «{company_title}» заблокирована администратором.",
            ),
            "deactivate": (
                "Компания деактивирована",
                f"Компания «{company_title}» деактивирована администратором.",
            ),
            "reactivate": (
                "Компания активирована",
                f"Доступ компании «{company_title}» восстановлен.",
            ),
        }
        title, body = messages[action]
        if reason:
            body = f"{body} Причина: {reason.strip()}"
        return title, body

    async def list_catalog_items(
        self,
        page: int = 1,
        page_size: int = 20,
        view: str = "all",
        query: str | None = None,
    ) -> dict:
        search_filters = [CatalogItem.status != ItemStatus.deleted]
        normalized_query = query.strip() if query else ""
        if normalized_query:
            search_pattern = f"%{normalized_query}%"
            search_conditions = [
                CatalogItem.title.ilike(search_pattern),
                CatalogItem.description.ilike(search_pattern),
                cast(CatalogItem.id, String).ilike(search_pattern),
            ]
            if normalized_query.isdigit():
                search_conditions.append(CatalogItem.id == int(normalized_query))
            search_filters.append(or_(*search_conditions))

        view_filter = self._catalog_view_filter(view)
        filters = [*search_filters]
        if view_filter is not None:
            filters.append(view_filter)

        total_result = await self.db.execute(
            select(func.count()).select_from(CatalogItem).where(*filters)
        )
        total = int(total_result.scalar_one())

        count_values: dict[str, int] = {}
        for count_view in ("all", "products", "services", "draft", "reported", "hidden"):
            count_filter = self._catalog_view_filter(count_view)
            count_filters = [*search_filters]
            if count_filter is not None:
                count_filters.append(count_filter)
            count_result = await self.db.execute(
                select(func.count()).select_from(CatalogItem).where(*count_filters)
            )
            count_values[count_view] = int(count_result.scalar_one())

        items_result = await self.db.execute(
            select(CatalogItem)
            .where(*filters)
            .options(
                selectinload(CatalogItem.category),
                selectinload(CatalogItem.pricing),
                selectinload(CatalogItem.media),
                selectinload(CatalogItem.stats),
                selectinload(CatalogItem.reports),
            )
            .order_by(CatalogItem.created_at.desc(), CatalogItem.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = items_result.scalars().unique().all()
        actor_ids = [item.actor_id for item in items]
        owners = await self._load_catalog_owners(actor_ids)

        return {
            "items": [
                self._serialize_catalog_list_item(item, owners.get(item.actor_id))
                for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
            "view_counts": count_values,
        }

    async def get_catalog_item_detail(self, item_id: int) -> dict:
        result = await self.db.execute(
            select(CatalogItem)
            .where(CatalogItem.id == item_id)
            .options(
                selectinload(CatalogItem.category),
                selectinload(CatalogItem.attributes),
                selectinload(CatalogItem.pricing),
                selectinload(CatalogItem.media),
                selectinload(CatalogItem.stats),
                selectinload(CatalogItem.reports).selectinload(CatalogItemReport.reporter),
            )
        )
        item = result.scalar_one_or_none()
        if not item or item.status == ItemStatus.deleted:
            raise NotFoundError("Catalog item not found")

        owners = await self._load_catalog_owners([item.actor_id])
        owner = owners.get(item.actor_id)
        history_result = await self.db.execute(
            select(AuditLog)
            .where(
                AuditLog.resource_type == "catalog_item",
                AuditLog.resource_id == str(item.id),
            )
            .options(selectinload(AuditLog.user))
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(50)
        )
        history = history_result.scalars().all()

        return {
            **self._serialize_catalog_list_item(item, owner),
            "description": item.description,
            "updated_at": item.created_at,
            "category": (
                {
                    "id": item.category.id,
                    "name": item.category.name,
                    "slug": item.category.slug,
                }
                if item.category
                else None
            ),
            "attributes": [
                {
                    "id": attribute.id,
                    "name": attribute.name,
                    "value": attribute.value,
                    "value_type": attribute.value_type,
                    "sort_order": attribute.sort_order,
                }
                for attribute in sorted(item.attributes, key=lambda value: value.sort_order)
            ],
            "pricing": (
                {
                    "pricing_type": item.pricing.pricing_type.value,
                    "currency": item.pricing.currency,
                    "fixed_price": item.pricing.fixed_price,
                    "hourly_rate": item.pricing.hourly_rate,
                    "monthly_rate": item.pricing.monthly_rate,
                    "tiers": item.pricing.tiers or [],
                }
                if item.pricing
                else None
            ),
            "media": [
                {
                    "id": media.id,
                    "file_name": media.file_name,
                    "file_url": public_file_url(media.file_url),
                    "media_type": media.media_type.value,
                    "sort_order": media.sort_order,
                }
                for media in sorted(item.media, key=lambda value: value.sort_order)
            ],
            "stats": (
                {
                    "views": item.stats.views,
                    "leads": item.stats.leads,
                }
                if item.stats
                else {"views": 0, "leads": 0}
            ),
            "owner": owner,
            "reports": [
                {
                    "id": report.id,
                    "reason": report.reason.value,
                    "details": report.details,
                    "status": report.status.value,
                    "created_at": report.created_at,
                    "resolved_at": report.resolved_at,
                    "reporter": {
                        "id": report.reporter.id,
                        "email": report.reporter.email,
                        "name": f"{report.reporter.first_name} {report.reporter.last_name}".strip(),
                    },
                }
                for report in sorted(
                    item.reports,
                    key=lambda value: value.created_at,
                    reverse=True,
                )
            ],
            "history": [
                {
                    "id": entry.id,
                    "action": entry.action,
                    "details": entry.details or {},
                    "created_at": entry.created_at,
                    "actor": (
                        {
                            "id": entry.user.id,
                            "email": entry.user.email,
                            "name": f"{entry.user.first_name} {entry.user.last_name}".strip(),
                        }
                        if entry.user
                        else None
                    ),
                }
                for entry in history
            ],
        }

    async def apply_catalog_action(
        self,
        item_id: int,
        action: str,
        current_user: User,
        reason: str | None = None,
    ) -> dict:
        result = await self.db.execute(
            select(CatalogItem)
            .where(CatalogItem.id == item_id)
            .options(selectinload(CatalogItem.reports))
        )
        item = result.scalar_one_or_none()
        if not item or item.status == ItemStatus.deleted:
            raise NotFoundError("Catalog item not found")

        allowed = {"approve", "hide", "request_changes", "delete"}
        if action not in allowed:
            raise ValidationError("Unsupported catalog action")
        if action == "delete" and current_user.role == UserRole.moderator:
            raise ForbiddenError("Moderator cannot delete catalog items")
        if action in {"hide", "request_changes", "delete"} and not (reason and reason.strip()):
            raise ValidationError("Reason is required for this action")

        previous_status = item.status.value
        if action == "approve":
            item.status = ItemStatus.active
            for report in item.reports:
                if report.status == CatalogItemReportStatus.open:
                    report.status = CatalogItemReportStatus.dismissed
                    report.resolved_at = datetime.now(timezone.utc)
                    report.resolved_by_id = current_user.id
        elif action == "hide":
            item.status = ItemStatus.hidden
            for report in item.reports:
                if report.status == CatalogItemReportStatus.open:
                    report.status = CatalogItemReportStatus.resolved
                    report.resolved_at = datetime.now(timezone.utc)
                    report.resolved_by_id = current_user.id
        elif action == "request_changes":
            item.status = ItemStatus.changes_requested
        elif action == "delete":
            item.status = ItemStatus.deleted
            for report in item.reports:
                if report.status == CatalogItemReportStatus.open:
                    report.status = CatalogItemReportStatus.resolved
                    report.resolved_at = datetime.now(timezone.utc)
                    report.resolved_by_id = current_user.id

        owner_user_id = await self._resolve_catalog_owner_user_id(item.actor_id)
        if owner_user_id:
            title, body = self._catalog_action_notification(action, item.title, reason)
            self.db.add(
                Notification(
                    user_id=owner_user_id,
                    company_id=None,
                    type=NotificationType.system,
                    title=title,
                    body=body,
                    href=f"/supplier/catalog/{item.id}",
                )
            )

        await log_audit(
            self.db,
            user_id=current_user.id,
            action=f"admin.catalog.{action}",
            resource_type="catalog_item",
            resource_id=str(item.id),
            details={
                "reason": reason.strip() if reason else None,
                "previous_status": previous_status,
                "status": item.status.value,
            },
        )
        await self.db.flush()
        return {
            "id": item.id,
            "action": action,
            "status": item.status.value,
        }

    @staticmethod
    def _catalog_view_filter(view: str):
        if view == "products":
            return CatalogItem.type == CatalogItemType.product
        if view == "services":
            return CatalogItem.type == CatalogItemType.service
        if view == "draft":
            return CatalogItem.status.in_(
                [
                    ItemStatus.draft,
                    ItemStatus.pending_review,
                    ItemStatus.changes_requested,
                ]
            )
        if view == "reported":
            return CatalogItem.id.in_(
                select(CatalogItemReport.item_id).where(
                    CatalogItemReport.status == CatalogItemReportStatus.open
                )
            )
        if view == "hidden":
            return CatalogItem.status == ItemStatus.hidden
        return None

    async def _load_catalog_owners(self, actor_ids: list[int]) -> dict[int, dict]:
        if not actor_ids:
            return {}
        actors_result = await self.db.execute(
            select(Actor)
            .where(Actor.id.in_(actor_ids))
            .options(
                selectinload(Actor.user),
                selectinload(Actor.company).selectinload(Company.owner),
            )
        )
        owners: dict[int, dict] = {}
        for actor in actors_result.scalars().unique().all():
            if actor.kind == ActorKind.company and actor.company:
                company = actor.company
                owner_user = company.owner
                owners[actor.id] = {
                    "actor_id": actor.id,
                    "actor_kind": actor.kind.value,
                    "display_name": actor.display_name,
                    "company_id": company.id,
                    "company_title": company.title,
                    "user_id": owner_user.id if owner_user else None,
                    "email": owner_user.email if owner_user else None,
                    "name": (
                        f"{owner_user.first_name} {owner_user.last_name}".strip()
                        if owner_user
                        else actor.display_name
                    ),
                }
            else:
                user = actor.user
                owners[actor.id] = {
                    "actor_id": actor.id,
                    "actor_kind": actor.kind.value,
                    "display_name": actor.display_name,
                    "company_id": None,
                    "company_title": None,
                    "user_id": user.id if user else None,
                    "email": user.email if user else None,
                    "name": (
                        f"{user.first_name} {user.last_name}".strip()
                        if user
                        else actor.display_name
                    ),
                }
        return owners

    async def _resolve_catalog_owner_user_id(self, actor_id: int) -> int | None:
        owners = await self._load_catalog_owners([actor_id])
        owner = owners.get(actor_id)
        return owner.get("user_id") if owner else None

    @staticmethod
    def _serialize_catalog_list_item(item: CatalogItem, owner: dict | None) -> dict:
        open_reports = sum(
            1 for report in item.reports if report.status == CatalogItemReportStatus.open
        )
        preview = next(
            (
                media.file_url
                for media in sorted(item.media, key=lambda value: value.sort_order)
                if media.media_type.value == "image"
            ),
            None,
        )
        return {
            "id": item.id,
            "title": item.title,
            "type": item.type.value,
            "status": item.status.value,
            "category_name": item.category.name if item.category else None,
            "preview_url": public_file_url(preview) if preview else preview,
            "open_reports_count": open_reports,
            "owner": owner,
            "created_at": item.created_at,
            "views": item.stats.views if item.stats else 0,
            "leads": item.stats.leads if item.stats else 0,
        }

    @staticmethod
    def _catalog_action_notification(
        action: str,
        item_title: str,
        reason: str | None,
    ) -> tuple[str, str]:
        messages = {
            "approve": (
                "Позиция одобрена",
                f"Позиция «{item_title}» опубликована в каталоге.",
            ),
            "hide": (
                "Позиция скрыта",
                f"Позиция «{item_title}» скрыта модератором.",
            ),
            "request_changes": (
                "Нужны изменения",
                f"Для позиции «{item_title}» запрошены правки перед публикацией.",
            ),
            "delete": (
                "Позиция удалена",
                f"Позиция «{item_title}» удалена администратором.",
            ),
        }
        title, body = messages[action]
        if reason:
            body = f"{body} Причина: {reason.strip()}"
        return title, body

    async def get_dashboard(self) -> dict:
        async def count(model, *filters) -> int:
            statement = select(func.count()).select_from(model)
            if filters:
                statement = statement.where(*filters)
            result = await self.db.execute(statement)
            return int(result.scalar_one())

        active_rfq_statuses = {
            RfqStatus.published,
            RfqStatus.receiving_proposals,
            RfqStatus.supplier_selected,
            RfqStatus.contract_created,
            RfqStatus.in_progress,
        }
        active_contract_statuses = {
            ContractStatus.pending_payment,
            ContractStatus.active,
            ContractStatus.delivered,
        }
        escrow_statuses = {
            PaymentMilestoneStatus.funded,
            PaymentMilestoneStatus.in_progress,
            PaymentMilestoneStatus.submitted,
            PaymentMilestoneStatus.approved,
            PaymentMilestoneStatus.disputed,
        }
        month_start = datetime.now(timezone.utc).replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

        escrow_result = await self.db.execute(
            select(func.coalesce(func.sum(PaymentMilestone.amount), 0.0)).where(
                PaymentMilestone.status.in_(escrow_statuses)
            )
        )
        revenue_result = await self.db.execute(
            select(func.coalesce(func.sum(SupplierInvoice.amount), 0.0)).where(
                SupplierInvoice.status == InvoiceStatus.paid,
                SupplierInvoice.paid_at >= month_start,
            )
        )

        metrics = {
            "total_users": await count(User),
            "total_companies": await count(Company),
            "catalog_items": await count(
                CatalogItem, CatalogItem.status != ItemStatus.deleted
            ),
            "active_rfqs": await count(Rfq, Rfq.status.in_(active_rfq_statuses)),
            "active_contracts": await count(
                Contract,
                Contract.status.in_(active_contract_statuses),
            ),
            "escrow_balance": float(escrow_result.scalar_one()),
            "open_disputes": await count(
                Dispute,
                Dispute.status.in_(
                    [
                        DisputeStatus.open,
                        DisputeStatus.under_review,
                        DisputeStatus.appealed,
                    ]
                ),
            ),
            "monthly_revenue": float(revenue_result.scalar_one()),
            "pending_verifications": await count(
                Company,
                Company.verification_status == VerificationStatus.pending,
            ),
        }

        recent_users = (
            await self.db.execute(select(User).order_by(User.created_at.desc()).limit(5))
        ).scalars().all()
        recent_contracts = (
            await self.db.execute(
                select(Contract).order_by(Contract.created_at.desc()).limit(5)
            )
        ).scalars().all()
        recent_payments = (
            await self.db.execute(
                select(SupplierInvoice)
                .where(SupplierInvoice.status == InvoiceStatus.paid)
                .order_by(SupplierInvoice.paid_at.desc())
                .limit(5)
            )
        ).scalars().all()
        recent_disputes = (
            await self.db.execute(
                select(Dispute)
                .where(
                    Dispute.status.in_(
                        [
                            DisputeStatus.open,
                            DisputeStatus.under_review,
                            DisputeStatus.appealed,
                        ]
                    )
                )
                .options(selectinload(Dispute.contract))
                .order_by(Dispute.created_at.desc())
                .limit(5)
            )
        ).scalars().all()

        activity = [
            {
                "id": f"user-{user.id}",
                "type": "registration",
                "title": f"{user.first_name} {user.last_name}".strip() or user.email,
                "description": user.email,
                "happened_at": user.created_at,
            }
            for user in recent_users
        ]
        activity.extend(
            {
                "id": f"contract-{contract.id}",
                "type": "contract",
                "title": contract.title,
                "description": f"Контракт #{contract.id}",
                "happened_at": contract.created_at,
            }
            for contract in recent_contracts
        )
        activity.extend(
            {
                "id": f"payment-{invoice.id}",
                "type": "payment",
                "title": invoice.title,
                "description": f"{invoice.amount:g} {invoice.currency}",
                "happened_at": invoice.paid_at or invoice.issued_at,
            }
            for invoice in recent_payments
        )
        activity.extend(
            {
                "id": f"dispute-{dispute.id}",
                "type": "dispute",
                "title": dispute.contract.title if dispute.contract else f"Спор #{dispute.id}",
                "description": f"Спор #{dispute.id} · контракт #{dispute.contract_id}",
                "happened_at": dispute.created_at,
            }
            for dispute in recent_disputes
        )
        activity.sort(key=lambda item: item["happened_at"], reverse=True)

        return {"metrics": metrics, "recent_activity": activity[:12]}

    async def update_user_status(
        self,
        user_id: int,
        status: str,
        current_user: User,
    ) -> UserPublic:
        result = await self.db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise NotFoundError("User not found")
        if user.id == current_user.id:
            raise ForbiddenError("You cannot change your own account status")

        role_rank = {
            UserRole.moderator: 1,
            UserRole.admin: 2,
            UserRole.superadmin: 3,
        }
        current_rank = role_rank.get(current_user.role, 0)
        target_rank = role_rank.get(user.role, 0)
        if target_rank and target_rank >= current_rank:
            raise ForbiddenError("You cannot change this staff account status")

        user.status = UserStatus(status)
        await self.db.flush()
        return UserPublic.model_validate(user)

    async def list_pending_verification(self) -> list[dict]:
        result = await self.db.execute(
            select(Company).where(Company.verification_status == VerificationStatus.pending)
        )
        return [
            {
                "id": c.id,
                "title": c.title,
                "actor_type": c.actor_type.value,
                "owner_id": c.owner_id,
                "verification_status": c.verification_status.value,
            }
            for c in result.scalars().all()
        ]

    async def verify_company(self, company_id: int, approved: bool) -> dict:
        result = await self.db.execute(select(Company).where(Company.id == company_id))
        company = result.scalar_one_or_none()
        if not company:
            raise NotFoundError("Company not found")
        company.verification_status = (
            VerificationStatus.verified if approved else VerificationStatus.rejected
        )
        await self.db.flush()
        return {"id": company.id, "verification_status": company.verification_status.value}

    async def list_disputes(
        self,
        page: int = 1,
        page_size: int = 20,
        view: str = "open",
        query: str | None = None,
    ) -> dict:
        search_filters: list = []
        normalized_query = query.strip() if query else ""
        if normalized_query:
            search_pattern = f"%{normalized_query}%"
            search_conditions = [
                Contract.title.ilike(search_pattern),
                cast(Dispute.id, String).ilike(search_pattern),
                cast(Dispute.contract_id, String).ilike(search_pattern),
            ]
            if normalized_query.isdigit():
                value = int(normalized_query)
                search_conditions.extend(
                    [Dispute.id == value, Dispute.contract_id == value]
                )
            search_filters.append(or_(*search_conditions))

        view_filter = self._dispute_view_filter(view)
        filters = [*search_filters]
        if view_filter is not None:
            filters.append(view_filter)

        count_stmt = (
            select(func.count())
            .select_from(Dispute)
            .join(Contract, Contract.id == Dispute.contract_id)
        )
        if filters:
            count_stmt = count_stmt.where(*filters)
        total = int((await self.db.execute(count_stmt)).scalar_one())

        count_values: dict[str, int] = {}
        for count_view in ("open", "under_review", "resolved", "appealed"):
            count_filter = self._dispute_view_filter(count_view)
            count_filters = [*search_filters]
            if count_filter is not None:
                count_filters.append(count_filter)
            view_count_stmt = (
                select(func.count())
                .select_from(Dispute)
                .join(Contract, Contract.id == Dispute.contract_id)
            )
            if count_filters:
                view_count_stmt = view_count_stmt.where(*count_filters)
            count_values[count_view] = int(
                (await self.db.execute(view_count_stmt)).scalar_one()
            )

        items_stmt = (
            select(Dispute)
            .join(Contract, Contract.id == Dispute.contract_id)
            .options(selectinload(Dispute.contract))
        )
        if filters:
            items_stmt = items_stmt.where(*filters)
        items_result = await self.db.execute(
            items_stmt.order_by(Dispute.created_at.desc(), Dispute.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = items_result.scalars().unique().all()
        actor_ids: list[int] = []
        for item in items:
            if item.contract:
                actor_ids.extend(
                    [item.contract.buyer_actor_id, item.contract.supplier_actor_id]
                )
            if item.opened_by_actor_id:
                actor_ids.append(item.opened_by_actor_id)
        parties = await self._load_catalog_owners(actor_ids)

        return {
            "items": [
                self._serialize_dispute_list_item(item, parties) for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size) if total else 1,
            "view_counts": count_values,
        }

    async def get_dispute_detail(self, dispute_id: int) -> dict:
        result = await self.db.execute(
            select(Dispute)
            .where(Dispute.id == dispute_id)
            .options(
                selectinload(Dispute.evidence),
                selectinload(Dispute.contract)
                .selectinload(Contract.payment_plan)
                .selectinload(PaymentPlan.milestones),
                selectinload(Dispute.contract).selectinload(Contract.files),
                selectinload(Dispute.contract)
                .selectinload(Contract.conversation)
                .selectinload(Conversation.messages),
                selectinload(Dispute.contract).selectinload(Contract.rfq),
                selectinload(Dispute.contract).selectinload(Contract.proposal),
            )
        )
        dispute = result.scalar_one_or_none()
        if not dispute or not dispute.contract:
            raise NotFoundError("Dispute not found")

        contract = dispute.contract
        actor_ids = [contract.buyer_actor_id, contract.supplier_actor_id]
        if dispute.opened_by_actor_id:
            actor_ids.append(dispute.opened_by_actor_id)
        parties = await self._load_catalog_owners(actor_ids)

        history_result = await self.db.execute(
            select(AuditLog)
            .where(
                AuditLog.resource_type == "dispute",
                AuditLog.resource_id == str(dispute.id),
            )
            .options(selectinload(AuditLog.user))
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(50)
        )
        history = history_result.scalars().all()

        messages = []
        if contract.conversation:
            for message in contract.conversation.messages:
                messages.append(
                    {
                        "id": message.id,
                        "sender_id": message.sender_id,
                        "text": message.text,
                        "created_at": message.created_at,
                    }
                )
        messages.sort(key=lambda item: item["created_at"], reverse=True)

        milestones = (
            list(contract.payment_plan.milestones) if contract.payment_plan else []
        )
        escrow = self._escrow_summary(milestones, contract.currency.value)

        return {
            **self._serialize_dispute_list_item(dispute, parties),
            "buyer_statement": dispute.buyer_statement,
            "supplier_statement": dispute.supplier_statement,
            "admin_instructions": dispute.admin_instructions,
            "resolution": dispute.resolution.value if dispute.resolution else None,
            "resolution_note": dispute.resolution_note,
            "partial_buyer_amount": dispute.partial_buyer_amount,
            "resolved_at": dispute.resolved_at,
            "buyer": parties.get(contract.buyer_actor_id),
            "supplier": parties.get(contract.supplier_actor_id),
            "contract": {
                "id": contract.id,
                "title": contract.title,
                "status": contract.status.value,
                "agreed_amount": contract.agreed_amount,
                "currency": contract.currency.value,
                "rfq_id": contract.rfq_id,
                "proposal_id": contract.proposal_id,
                "description": contract.description,
            },
            "evidence": [
                {
                    "id": item.id,
                    "file_name": item.file_name,
                    "file_url": public_file_url(item.file_url),
                    "file_type": item.file_type,
                    "note": item.note,
                    "uploaded_by_actor_id": item.uploaded_by_actor_id,
                    "created_at": item.created_at,
                }
                for item in sorted(
                    dispute.evidence, key=lambda value: value.created_at, reverse=True
                )
            ],
            "files": [
                {
                    "id": file.id,
                    "file_name": file.file_name,
                    "file_url": public_file_url(file.file_url),
                    "file_type": file.file_type,
                    "uploaded_by": file.uploaded_by,
                    "created_at": file.created_at,
                }
                for file in sorted(
                    contract.files, key=lambda value: value.created_at, reverse=True
                )
            ],
            "messages": messages,
            "escrow": escrow,
            "timeline": [
                {
                    "id": entry.id,
                    "action": entry.action,
                    "details": entry.details or {},
                    "created_at": entry.created_at,
                    "actor": (
                        {
                            "id": entry.user.id,
                            "email": entry.user.email,
                            "name": f"{entry.user.first_name} {entry.user.last_name}".strip(),
                        }
                        if entry.user
                        else None
                    ),
                }
                for entry in history
            ],
        }

    async def apply_dispute_action(
        self,
        dispute_id: int,
        action: str,
        current_user: User,
        reason: str | None = None,
        partial_buyer_amount: float | None = None,
    ) -> dict:
        result = await self.db.execute(
            select(Dispute)
            .where(Dispute.id == dispute_id)
            .options(
                selectinload(Dispute.contract)
                .selectinload(Contract.payment_plan)
                .selectinload(PaymentPlan.milestones),
            )
        )
        dispute = result.scalar_one_or_none()
        if not dispute or not dispute.contract:
            raise NotFoundError("Dispute not found")

        allowed = {
            "release_funds",
            "refund_buyer",
            "partial_refund",
            "request_evidence",
            "close_case",
        }
        if action not in allowed:
            raise ValidationError("Unsupported dispute action")
        if not (reason and reason.strip()):
            raise ValidationError("Reason is required for this action")
        if dispute.status == DisputeStatus.resolved:
            raise ConflictError("Dispute is already resolved")

        contract = dispute.contract
        milestones = (
            list(contract.payment_plan.milestones) if contract.payment_plan else []
        )
        movable = {
            PaymentMilestoneStatus.funded,
            PaymentMilestoneStatus.in_progress,
            PaymentMilestoneStatus.submitted,
            PaymentMilestoneStatus.approved,
            PaymentMilestoneStatus.awaiting_payment,
            PaymentMilestoneStatus.disputed,
        }
        previous_status = dispute.status.value
        now = datetime.now(timezone.utc)

        if action == "request_evidence":
            dispute.status = DisputeStatus.under_review
            dispute.admin_instructions = reason.strip()
        elif action == "release_funds":
            for milestone in milestones:
                if milestone.status in movable:
                    milestone.status = PaymentMilestoneStatus.released
            dispute.status = DisputeStatus.resolved
            dispute.resolution = DisputeResolution.release_funds
            dispute.resolution_note = reason.strip()
            dispute.resolved_at = now
            dispute.resolved_by_id = current_user.id
            contract.status = ContractStatus.completed
        elif action == "refund_buyer":
            for milestone in milestones:
                if milestone.status in movable:
                    milestone.status = PaymentMilestoneStatus.refunded
            dispute.status = DisputeStatus.resolved
            dispute.resolution = DisputeResolution.refund_buyer
            dispute.resolution_note = reason.strip()
            dispute.resolved_at = now
            dispute.resolved_by_id = current_user.id
            contract.status = ContractStatus.cancelled
            from src.modules.finance.ledger import FinanceLedgerService
            from src.models import (
                PlatformPaymentGateway,
                PlatformPaymentStatus,
                PlatformPaymentType,
            )

            refund_amount = sum(m.amount for m in milestones if m.status == PaymentMilestoneStatus.refunded)
            if refund_amount > 0:
                await FinanceLedgerService(self.db).record(
                    payment_type=PlatformPaymentType.refund,
                    amount=refund_amount,
                    currency=contract.currency.value,
                    title=f"Возврат · контракт #{contract.id}",
                    description=reason.strip(),
                    status=PlatformPaymentStatus.paid,
                    gateway=PlatformPaymentGateway.manual,
                    actor_id=contract.buyer_actor_id,
                    contract_id=contract.id,
                    metadata={"dispute_id": dispute.id, "action": "refund_buyer"},
                )
        elif action == "partial_refund":
            if partial_buyer_amount is None or partial_buyer_amount <= 0:
                raise ValidationError("partial_buyer_amount is required")
            pool = sum(m.amount for m in milestones if m.status in movable)
            if partial_buyer_amount > pool + 1e-6:
                raise ValidationError("partial_buyer_amount exceeds escrow pool")
            self._apply_partial_refund(milestones, movable, partial_buyer_amount)
            dispute.status = DisputeStatus.resolved
            dispute.resolution = DisputeResolution.partial_refund
            dispute.resolution_note = reason.strip()
            dispute.partial_buyer_amount = partial_buyer_amount
            dispute.resolved_at = now
            dispute.resolved_by_id = current_user.id
            contract.status = ContractStatus.completed
            from src.modules.finance.ledger import FinanceLedgerService
            from src.models import (
                PlatformPaymentGateway,
                PlatformPaymentStatus,
                PlatformPaymentType,
            )

            await FinanceLedgerService(self.db).record(
                payment_type=PlatformPaymentType.refund,
                amount=partial_buyer_amount,
                currency=contract.currency.value,
                title=f"Частичный возврат · контракт #{contract.id}",
                description=reason.strip(),
                status=PlatformPaymentStatus.paid,
                gateway=PlatformPaymentGateway.manual,
                actor_id=contract.buyer_actor_id,
                contract_id=contract.id,
                metadata={
                    "dispute_id": dispute.id,
                    "action": "partial_refund",
                },
            )
        elif action == "close_case":
            dispute.status = DisputeStatus.resolved
            dispute.resolution = DisputeResolution.close_case
            dispute.resolution_note = reason.strip()
            dispute.resolved_at = now
            dispute.resolved_by_id = current_user.id
            contract.status = ContractStatus.completed

        for actor_id in (contract.buyer_actor_id, contract.supplier_actor_id):
            user_id = await self._resolve_catalog_owner_user_id(actor_id)
            if not user_id:
                continue
            title, body = self._dispute_action_notification(
                action, contract.title, reason
            )
            self.db.add(
                Notification(
                    user_id=user_id,
                    company_id=None,
                    type=NotificationType.system,
                    title=title,
                    body=body,
                    href=f"/customer/contracts/{contract.id}",
                )
            )

        await log_audit(
            self.db,
            user_id=current_user.id,
            action=f"admin.dispute.{action}",
            resource_type="dispute",
            resource_id=str(dispute.id),
            details={
                "reason": reason.strip() if reason else None,
                "previous_status": previous_status,
                "status": dispute.status.value,
                "partial_buyer_amount": partial_buyer_amount,
                "contract_id": contract.id,
            },
        )
        await self.db.flush()
        return {
            "id": dispute.id,
            "action": action,
            "status": dispute.status.value,
            "resolution": dispute.resolution.value if dispute.resolution else None,
            "contract_status": contract.status.value,
        }

    @staticmethod
    def _dispute_view_filter(view: str):
        if view == "open":
            return Dispute.status == DisputeStatus.open
        if view == "under_review":
            return Dispute.status == DisputeStatus.under_review
        if view == "resolved":
            return Dispute.status == DisputeStatus.resolved
        if view == "appealed":
            return Dispute.status == DisputeStatus.appealed
        return None

    @staticmethod
    def _apply_partial_refund(
        milestones: list,
        movable: set,
        buyer_amount: float,
    ) -> None:
        remaining_refund = buyer_amount
        for milestone in milestones:
            if milestone.status not in movable:
                continue
            if remaining_refund <= 1e-9:
                milestone.status = PaymentMilestoneStatus.released
                continue
            if milestone.amount <= remaining_refund + 1e-9:
                milestone.status = PaymentMilestoneStatus.refunded
                remaining_refund -= milestone.amount
            else:
                if remaining_refund >= milestone.amount / 2:
                    milestone.status = PaymentMilestoneStatus.refunded
                else:
                    milestone.status = PaymentMilestoneStatus.released
                remaining_refund = 0

    def _serialize_dispute_list_item(
        self,
        dispute: Dispute,
        parties: dict[int, dict],
    ) -> dict:
        contract = dispute.contract
        return {
            "id": dispute.id,
            "status": dispute.status.value,
            "contract_id": dispute.contract_id,
            "contract_title": contract.title if contract else None,
            "contract_amount": contract.agreed_amount if contract else None,
            "currency": contract.currency.value if contract else None,
            "opened_by_actor_id": dispute.opened_by_actor_id,
            "opened_by": (
                parties.get(dispute.opened_by_actor_id)
                if dispute.opened_by_actor_id
                else None
            ),
            "buyer": parties.get(contract.buyer_actor_id) if contract else None,
            "supplier": parties.get(contract.supplier_actor_id) if contract else None,
            "created_at": dispute.created_at,
            "updated_at": dispute.updated_at,
        }

    @staticmethod
    def _dispute_action_notification(
        action: str, contract_title: str, reason: str | None
    ) -> tuple[str, str]:
        messages = {
            "release_funds": (
                "Спор: выплата исполнителю",
                f"По спору по контракту «{contract_title}» средства выплачены исполнителю.",
            ),
            "refund_buyer": (
                "Спор: возврат покупателю",
                f"По спору по контракту «{contract_title}» средства возвращены покупателю.",
            ),
            "partial_refund": (
                "Спор: частичный возврат",
                f"По спору по контракту «{contract_title}» выполнен частичный возврат.",
            ),
            "request_evidence": (
                "Спор: запрошены доказательства",
                f"Администратор запросил дополнительные доказательства по контракту «{contract_title}».",
            ),
            "close_case": (
                "Спор закрыт",
                f"Спор по контракту «{contract_title}» закрыт администратором.",
            ),
        }
        title, body = messages[action]
        if reason:
            body = f"{body} Причина: {reason.strip()}"
        return title, body

    async def list_rfqs(
        self,
        page: int = 1,
        page_size: int = 20,
        view: str = "published",
        query: str | None = None,
    ) -> dict:
        search_filters: list = []
        normalized_query = query.strip() if query else ""
        if normalized_query:
            search_pattern = f"%{normalized_query}%"
            search_filters.append(
                or_(
                    Rfq.title.ilike(search_pattern),
                    Rfq.description.ilike(search_pattern),
                    Rfq.id.ilike(search_pattern),
                    Rfq.category_id.ilike(search_pattern),
                )
            )

        view_filter = self._rfq_view_filter(view)
        filters = [*search_filters]
        if view_filter is not None:
            filters.append(view_filter)

        count_stmt = select(func.count()).select_from(Rfq)
        if filters:
            count_stmt = count_stmt.where(*filters)
        total = int((await self.db.execute(count_stmt)).scalar_one())

        count_values: dict[str, int] = {}
        for count_view in ("published", "closed", "draft", "reported", "archived"):
            count_filter = self._rfq_view_filter(count_view)
            count_filters = [*search_filters]
            if count_filter is not None:
                count_filters.append(count_filter)
            view_count_stmt = select(func.count()).select_from(Rfq)
            if count_filters:
                view_count_stmt = view_count_stmt.where(*count_filters)
            count_values[count_view] = int(
                (await self.db.execute(view_count_stmt)).scalar_one()
            )

        items_stmt = select(Rfq).options(
            selectinload(Rfq.reports),
            selectinload(Rfq.proposals),
            selectinload(Rfq.attachments),
        )
        if filters:
            items_stmt = items_stmt.where(*filters)
        items_result = await self.db.execute(
            items_stmt.order_by(Rfq.updated_at.desc(), Rfq.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = items_result.scalars().unique().all()
        buyers = await self._load_catalog_owners([item.actor_id for item in items])

        return {
            "items": [
                self._serialize_rfq_list_item(item, buyers.get(item.actor_id))
                for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size) if total else 1,
            "view_counts": count_values,
        }

    async def get_rfq_detail(self, rfq_id: str) -> dict:
        result = await self.db.execute(
            select(Rfq)
            .where(Rfq.id == rfq_id)
            .options(
                selectinload(Rfq.attachments),
                selectinload(Rfq.reports).selectinload(RfqReport.reporter),
                selectinload(Rfq.proposals).selectinload(Proposal.attachment),
                selectinload(Rfq.contracts)
                .selectinload(Contract.conversation)
                .selectinload(Conversation.messages),
            )
        )
        rfq = result.scalar_one_or_none()
        if not rfq:
            raise NotFoundError("RFQ not found")

        buyers = await self._load_catalog_owners([rfq.actor_id])
        buyer = buyers.get(rfq.actor_id)
        suppliers = await self._load_catalog_owners(
            [p.supplier_actor_id for p in rfq.proposals]
        )

        history_result = await self.db.execute(
            select(AuditLog)
            .where(AuditLog.resource_type == "rfq", AuditLog.resource_id == rfq.id)
            .options(selectinload(AuditLog.user))
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(50)
        )
        history = history_result.scalars().all()

        messages: list[dict] = []
        for proposal in rfq.proposals:
            if proposal.message:
                supplier = suppliers.get(proposal.supplier_actor_id)
                messages.append(
                    {
                        "id": f"proposal-{proposal.id}",
                        "source": "proposal",
                        "proposal_id": proposal.id,
                        "contract_id": None,
                        "sender_name": (
                            (supplier.get("company_title") or supplier.get("name"))
                            if supplier
                            else f"Supplier #{proposal.supplier_actor_id}"
                        ),
                        "text": proposal.message,
                        "created_at": proposal.created_at,
                    }
                )
        for contract in rfq.contracts:
            if not contract.conversation:
                continue
            for message in contract.conversation.messages:
                messages.append(
                    {
                        "id": f"message-{message.id}",
                        "source": "contract",
                        "proposal_id": contract.proposal_id,
                        "contract_id": contract.id,
                        "sender_name": f"User #{message.sender_id}",
                        "text": message.text,
                        "created_at": message.created_at,
                    }
                )
        messages.sort(key=lambda item: item["created_at"], reverse=True)

        return {
            **self._serialize_rfq_list_item(rfq, buyer),
            "description": rfq.description,
            "updated_at": rfq.updated_at,
            "requirements": {
                "type": rfq.type.value,
                "category_id": rfq.category_id,
                "budget_type": rfq.budget_type.value,
                "budget_from": rfq.budget_from,
                "budget_to": rfq.budget_to,
                "currency": rfq.currency,
                "deadline": rfq.deadline,
                "visibility": rfq.visibility.value,
                "quantity": rfq.quantity,
                "delivery_country": rfq.delivery_country,
                "delivery_city": rfq.delivery_city,
                "delivery_address": rfq.delivery_address,
                "delivery_date": rfq.delivery_date,
                "project_duration": rfq.project_duration,
                "start_date": rfq.start_date,
                "team_size_required": rfq.team_size_required,
                "experience_required": rfq.experience_required,
                "attachments": [
                    {
                        "id": attachment.id,
                        "file_name": attachment.file_name,
                        "file_url": public_file_url(attachment.file_url),
                        "file_type": attachment.file_type,
                    }
                    for attachment in rfq.attachments
                ],
            },
            "buyer": buyer,
            "proposals": [
                {
                    "id": proposal.id,
                    "supplier_actor_id": proposal.supplier_actor_id,
                    "supplier": suppliers.get(proposal.supplier_actor_id),
                    "price": proposal.price,
                    "currency": proposal.currency.value,
                    "delivery_time": proposal.delivery_time,
                    "message": proposal.message,
                    "status": proposal.status.value,
                    "created_at": proposal.created_at,
                    "has_attachment": proposal.attachment is not None,
                }
                for proposal in sorted(
                    rfq.proposals, key=lambda value: value.created_at, reverse=True
                )
            ],
            "messages": messages,
            "reports": [
                {
                    "id": report.id,
                    "reason": report.reason.value,
                    "details": report.details,
                    "status": report.status.value,
                    "created_at": report.created_at,
                    "resolved_at": report.resolved_at,
                    "reporter": {
                        "id": report.reporter.id,
                        "email": report.reporter.email,
                        "name": f"{report.reporter.first_name} {report.reporter.last_name}".strip(),
                    },
                }
                for report in sorted(
                    rfq.reports, key=lambda value: value.created_at, reverse=True
                )
            ],
            "history": [
                {
                    "id": entry.id,
                    "action": entry.action,
                    "details": entry.details or {},
                    "created_at": entry.created_at,
                    "actor": (
                        {
                            "id": entry.user.id,
                            "email": entry.user.email,
                            "name": f"{entry.user.first_name} {entry.user.last_name}".strip(),
                        }
                        if entry.user
                        else None
                    ),
                }
                for entry in history
            ],
        }

    async def apply_rfq_action(
        self,
        rfq_id: str,
        action: str,
        current_user: User,
        reason: str | None = None,
    ) -> dict:
        result = await self.db.execute(
            select(Rfq).where(Rfq.id == rfq_id).options(selectinload(Rfq.reports))
        )
        rfq = result.scalar_one_or_none()
        if not rfq:
            raise NotFoundError("RFQ not found")

        allowed = {"hide", "close", "delete", "warn_buyer"}
        if action not in allowed:
            raise ValidationError("Unsupported RFQ action")
        if action == "delete" and current_user.role == UserRole.moderator:
            raise ForbiddenError("Moderator cannot delete RFQs")
        if action in {"hide", "close", "delete", "warn_buyer"} and not (
            reason and reason.strip()
        ):
            raise ValidationError("Reason is required for this action")

        previous_status = rfq.status.value
        terminal = {
            RfqStatus.completed,
            RfqStatus.cancelled,
            RfqStatus.expired,
            RfqStatus.archived,
        }

        if action == "hide":
            rfq.status = RfqStatus.archived
            self._resolve_open_rfq_reports(rfq, current_user.id, RfqReportStatus.resolved)
        elif action == "close":
            if rfq.status in terminal:
                raise ConflictError("RFQ is already closed")
            rfq.status = RfqStatus.cancelled
        elif action == "delete":
            if rfq.status == RfqStatus.draft:
                attachments = (
                    await self.db.execute(
                        select(Rfq).where(Rfq.id == rfq_id).options(
                            selectinload(Rfq.attachments),
                            selectinload(Rfq.invited_suppliers),
                            selectinload(Rfq.reports),
                        )
                    )
                ).scalar_one()
                for attachment in list(attachments.attachments):
                    await self.db.delete(attachment)
                for invited in list(attachments.invited_suppliers):
                    await self.db.delete(invited)
                for report in list(attachments.reports):
                    await self.db.delete(report)
                await self.db.delete(attachments)
                await log_audit(
                    self.db,
                    user_id=current_user.id,
                    action="admin.rfq.delete",
                    resource_type="rfq",
                    resource_id=rfq_id,
                    details={
                        "reason": reason.strip() if reason else None,
                        "previous_status": previous_status,
                        "status": "deleted",
                    },
                )
                await self.db.flush()
                return {"id": rfq_id, "action": action, "status": "deleted"}
            rfq.status = RfqStatus.archived
            self._resolve_open_rfq_reports(rfq, current_user.id, RfqReportStatus.resolved)
        elif action == "warn_buyer":
            pass

        buyer_user_id = await self._resolve_catalog_owner_user_id(rfq.actor_id)
        if buyer_user_id:
            title, body = self._rfq_action_notification(action, rfq.title, reason)
            self.db.add(
                Notification(
                    user_id=buyer_user_id,
                    company_id=None,
                    type=NotificationType.system,
                    title=title,
                    body=body,
                    href=f"/customer/rfqs/{rfq.id}",
                )
            )

        await log_audit(
            self.db,
            user_id=current_user.id,
            action=f"admin.rfq.{action}",
            resource_type="rfq",
            resource_id=rfq.id,
            details={
                "reason": reason.strip() if reason else None,
                "previous_status": previous_status,
                "status": rfq.status.value,
            },
        )
        await self.db.flush()
        return {"id": rfq.id, "action": action, "status": rfq.status.value}

    async def list_proposals(
        self,
        page: int = 1,
        page_size: int = 20,
        view: str = "all",
        query: str | None = None,
    ) -> dict:
        search_filters: list = []
        normalized_query = query.strip() if query else ""
        if normalized_query:
            search_pattern = f"%{normalized_query}%"
            search_conditions = [
                Proposal.message.ilike(search_pattern),
                cast(Proposal.id, String).ilike(search_pattern),
                Proposal.rfq_id.ilike(search_pattern),
            ]
            if normalized_query.isdigit():
                search_conditions.append(Proposal.id == int(normalized_query))
            search_filters.append(or_(*search_conditions))

        view_filter = self._proposal_view_filter(view)
        filters = [*search_filters]
        if view_filter is not None:
            filters.append(view_filter)

        count_stmt = select(func.count()).select_from(Proposal)
        if filters:
            count_stmt = count_stmt.where(*filters)
        total = int((await self.db.execute(count_stmt)).scalar_one())

        count_values: dict[str, int] = {}
        for count_view in ("all", "pending", "accepted", "rejected", "reported"):
            count_filter = self._proposal_view_filter(count_view)
            count_filters = [*search_filters]
            if count_filter is not None:
                count_filters.append(count_filter)
            view_count_stmt = select(func.count()).select_from(Proposal)
            if count_filters:
                view_count_stmt = view_count_stmt.where(*count_filters)
            count_values[count_view] = int(
                (await self.db.execute(view_count_stmt)).scalar_one()
            )

        items_stmt = select(Proposal).options(
            selectinload(Proposal.reports),
            selectinload(Proposal.attachment),
            selectinload(Proposal.rfq),
            selectinload(Proposal.contract),
        )
        if filters:
            items_stmt = items_stmt.where(*filters)
        items_result = await self.db.execute(
            items_stmt.order_by(Proposal.created_at.desc(), Proposal.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = items_result.scalars().unique().all()
        actor_ids = [item.supplier_actor_id for item in items] + [
            item.rfq.actor_id for item in items if item.rfq
        ]
        parties = await self._load_catalog_owners(actor_ids)

        return {
            "items": [
                self._serialize_proposal_list_item(
                    item,
                    parties.get(item.supplier_actor_id),
                    parties.get(item.rfq.actor_id) if item.rfq else None,
                )
                for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size) if total else 1,
            "view_counts": count_values,
        }

    async def get_proposal_detail(self, proposal_id: int) -> dict:
        result = await self.db.execute(
            select(Proposal)
            .where(Proposal.id == proposal_id)
            .options(
                selectinload(Proposal.attachment),
                selectinload(Proposal.reports).selectinload(ProposalReport.reporter),
                selectinload(Proposal.rfq),
                selectinload(Proposal.contract)
                .selectinload(Contract.conversation)
                .selectinload(Conversation.messages),
            )
        )
        proposal = result.scalar_one_or_none()
        if not proposal:
            raise NotFoundError("Proposal not found")

        actor_ids = [proposal.supplier_actor_id]
        if proposal.rfq:
            actor_ids.append(proposal.rfq.actor_id)
        parties = await self._load_catalog_owners(actor_ids)
        supplier = parties.get(proposal.supplier_actor_id)
        buyer = parties.get(proposal.rfq.actor_id) if proposal.rfq else None

        history_result = await self.db.execute(
            select(AuditLog)
            .where(
                AuditLog.resource_type == "proposal",
                AuditLog.resource_id == str(proposal.id),
            )
            .options(selectinload(AuditLog.user))
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(50)
        )
        history = history_result.scalars().all()

        messages: list[dict] = []
        if proposal.message:
            messages.append(
                {
                    "id": f"proposal-{proposal.id}",
                    "source": "proposal",
                    "text": proposal.message,
                    "sender_name": (
                        (supplier.get("company_title") or supplier.get("name"))
                        if supplier
                        else f"Supplier #{proposal.supplier_actor_id}"
                    ),
                    "created_at": proposal.created_at,
                }
            )
        if proposal.contract and proposal.contract.conversation:
            for message in proposal.contract.conversation.messages:
                messages.append(
                    {
                        "id": f"message-{message.id}",
                        "source": "contract",
                        "text": message.text,
                        "sender_name": f"User #{message.sender_id}",
                        "created_at": message.created_at,
                    }
                )
        messages.sort(key=lambda item: item["created_at"], reverse=True)

        contract = proposal.contract
        return {
            **self._serialize_proposal_list_item(proposal, supplier, buyer),
            "delivery_time": proposal.delivery_time,
            "message": proposal.message,
            "attachment": (
                {
                    "id": proposal.attachment.id,
                    "file_name": proposal.attachment.file_name,
                    "file_url": public_file_url(proposal.attachment.file_url),
                    "file_type": proposal.attachment.file_type,
                }
                if proposal.attachment
                else None
            ),
            "supplier": supplier,
            "buyer": buyer,
            "rfq": (
                {
                    "id": proposal.rfq.id,
                    "title": proposal.rfq.title,
                    "status": proposal.rfq.status.value,
                }
                if proposal.rfq
                else None
            ),
            "contract": (
                {
                    "id": contract.id,
                    "title": contract.title,
                    "status": contract.status.value,
                    "agreed_amount": contract.agreed_amount,
                    "currency": contract.currency.value,
                    "created_at": contract.created_at,
                }
                if contract
                else None
            ),
            "messages": messages,
            "reports": [
                {
                    "id": report.id,
                    "reason": report.reason.value,
                    "details": report.details,
                    "status": report.status.value,
                    "created_at": report.created_at,
                    "resolved_at": report.resolved_at,
                    "reporter": {
                        "id": report.reporter.id,
                        "email": report.reporter.email,
                        "name": f"{report.reporter.first_name} {report.reporter.last_name}".strip(),
                    },
                }
                for report in sorted(
                    proposal.reports, key=lambda value: value.created_at, reverse=True
                )
            ],
            "history": [
                {
                    "id": entry.id,
                    "action": entry.action,
                    "details": entry.details or {},
                    "created_at": entry.created_at,
                    "actor": (
                        {
                            "id": entry.user.id,
                            "email": entry.user.email,
                            "name": f"{entry.user.first_name} {entry.user.last_name}".strip(),
                        }
                        if entry.user
                        else None
                    ),
                }
                for entry in history
            ],
        }

    async def apply_proposal_action(
        self,
        proposal_id: int,
        action: str,
        current_user: User,
        reason: str | None = None,
    ) -> dict:
        result = await self.db.execute(
            select(Proposal)
            .where(Proposal.id == proposal_id)
            .options(
                selectinload(Proposal.reports),
                selectinload(Proposal.contract),
                selectinload(Proposal.attachment),
            )
        )
        proposal = result.scalar_one_or_none()
        if not proposal:
            raise NotFoundError("Proposal not found")

        allowed = {"delete", "investigate", "block_supplier"}
        if action not in allowed:
            raise ValidationError("Unsupported proposal action")
        if action in {"delete", "block_supplier"} and current_user.role == UserRole.moderator:
            raise ForbiddenError("Moderator cannot perform this action")
        if action in {"delete", "investigate", "block_supplier"} and not (
            reason and reason.strip()
        ):
            raise ValidationError("Reason is required for this action")

        previous_status = proposal.status.value
        status = previous_status
        blocked_company_id = None

        if action == "delete":
            if proposal.contract is not None:
                raise ConflictError("Cannot delete proposal with an existing contract")
            if proposal.attachment is not None:
                await self.db.delete(proposal.attachment)
            for report in list(proposal.reports):
                await self.db.delete(report)
            await self.db.delete(proposal)
            await log_audit(
                self.db,
                user_id=current_user.id,
                action="admin.proposal.delete",
                resource_type="proposal",
                resource_id=str(proposal_id),
                details={
                    "reason": reason.strip() if reason else None,
                    "previous_status": previous_status,
                    "status": "deleted",
                },
            )
            await self.db.flush()
            return {"id": proposal_id, "action": action, "status": "deleted"}

        if action == "investigate":
            self._resolve_open_proposal_reports(
                proposal, current_user.id, ProposalReportStatus.resolved
            )
        elif action == "block_supplier":
            owners = await self._load_catalog_owners([proposal.supplier_actor_id])
            owner = owners.get(proposal.supplier_actor_id)
            company_id = owner.get("company_id") if owner else None
            if not company_id:
                raise ValidationError("Supplier has no company to block")
            blocked = await self.apply_company_action(
                company_id=company_id,
                action="block",
                current_user=current_user,
                reason=reason,
            )
            blocked_company_id = blocked["id"]

        await log_audit(
            self.db,
            user_id=current_user.id,
            action=f"admin.proposal.{action}",
            resource_type="proposal",
            resource_id=str(proposal.id),
            details={
                "reason": reason.strip() if reason else None,
                "previous_status": previous_status,
                "status": status,
                "blocked_company_id": blocked_company_id,
            },
        )
        await self.db.flush()
        return {
            "id": proposal.id,
            "action": action,
            "status": status,
            "blocked_company_id": blocked_company_id,
        }

    @staticmethod
    def _rfq_view_filter(view: str):
        if view == "published":
            return Rfq.status.in_(
                [
                    RfqStatus.published,
                    RfqStatus.receiving_proposals,
                    RfqStatus.supplier_selected,
                    RfqStatus.contract_created,
                    RfqStatus.in_progress,
                ]
            )
        if view == "closed":
            return Rfq.status.in_(
                [
                    RfqStatus.completed,
                    RfqStatus.cancelled,
                    RfqStatus.expired,
                    RfqStatus.disputed,
                ]
            )
        if view == "draft":
            return Rfq.status == RfqStatus.draft
        if view == "reported":
            return Rfq.id.in_(
                select(RfqReport.rfq_id).where(RfqReport.status == RfqReportStatus.open)
            )
        if view == "archived":
            return Rfq.status == RfqStatus.archived
        return None

    @staticmethod
    def _proposal_view_filter(view: str):
        if view == "pending":
            return Proposal.status.in_(
                [
                    ProposalStatus.submitted,
                    ProposalStatus.viewed,
                    ProposalStatus.shortlisted,
                ]
            )
        if view == "accepted":
            return Proposal.status == ProposalStatus.accepted
        if view == "rejected":
            return Proposal.status.in_(
                [ProposalStatus.rejected, ProposalStatus.withdrawn]
            )
        if view == "reported":
            return Proposal.id.in_(
                select(ProposalReport.proposal_id).where(
                    ProposalReport.status == ProposalReportStatus.open
                )
            )
        return None

    @staticmethod
    def _serialize_rfq_list_item(rfq: Rfq, buyer: dict | None) -> dict:
        open_reports = sum(
            1 for report in rfq.reports if report.status == RfqReportStatus.open
        )
        return {
            "id": rfq.id,
            "title": rfq.title,
            "type": rfq.type.value,
            "status": rfq.status.value,
            "category_id": rfq.category_id,
            "currency": rfq.currency,
            "budget_from": rfq.budget_from,
            "budget_to": rfq.budget_to,
            "deadline": rfq.deadline,
            "proposals_count": len(rfq.proposals) if rfq.proposals is not None else 0,
            "open_reports_count": open_reports,
            "buyer": buyer,
            "created_at": rfq.created_at,
            "updated_at": rfq.updated_at,
        }

    @staticmethod
    def _serialize_proposal_list_item(
        proposal: Proposal,
        supplier: dict | None,
        buyer: dict | None,
    ) -> dict:
        open_reports = sum(
            1
            for report in proposal.reports
            if report.status == ProposalReportStatus.open
        )
        return {
            "id": proposal.id,
            "rfq_id": proposal.rfq_id,
            "rfq_title": proposal.rfq.title if proposal.rfq else None,
            "price": proposal.price,
            "currency": proposal.currency.value,
            "status": proposal.status.value,
            "open_reports_count": open_reports,
            "has_contract": proposal.contract is not None,
            "supplier": supplier,
            "buyer": buyer,
            "created_at": proposal.created_at,
        }

    @staticmethod
    def _resolve_open_rfq_reports(
        rfq: Rfq, resolver_id: int, status: RfqReportStatus
    ) -> None:
        now = datetime.now(timezone.utc)
        for report in rfq.reports:
            if report.status == RfqReportStatus.open:
                report.status = status
                report.resolved_at = now
                report.resolved_by_id = resolver_id

    @staticmethod
    def _resolve_open_proposal_reports(
        proposal: Proposal, resolver_id: int, status: ProposalReportStatus
    ) -> None:
        now = datetime.now(timezone.utc)
        for report in proposal.reports:
            if report.status == ProposalReportStatus.open:
                report.status = status
                report.resolved_at = now
                report.resolved_by_id = resolver_id

    @staticmethod
    def _rfq_action_notification(
        action: str, rfq_title: str, reason: str | None
    ) -> tuple[str, str]:
        messages = {
            "hide": (
                "Заявка скрыта",
                f"Заявка «{rfq_title}» скрыта администратором платформы.",
            ),
            "close": (
                "Заявка закрыта",
                f"Заявка «{rfq_title}» закрыта администратором платформы.",
            ),
            "delete": (
                "Заявка удалена",
                f"Заявка «{rfq_title}» удалена администратором платформы.",
            ),
            "warn_buyer": (
                "Предупреждение по заявке",
                f"Администратор отправил предупреждение по заявке «{rfq_title}».",
            ),
        }
        title, body = messages[action]
        if reason:
            body = f"{body} Причина: {reason.strip()}"
        return title, body

    async def list_contracts(
        self,
        page: int = 1,
        page_size: int = 20,
        view: str = "active",
        query: str | None = None,
    ) -> dict:
        search_filters: list = []
        normalized_query = query.strip() if query else ""
        if normalized_query:
            search_pattern = f"%{normalized_query}%"
            search_conditions = [
                Contract.title.ilike(search_pattern),
                Contract.description.ilike(search_pattern),
                Contract.rfq_id.ilike(search_pattern),
                cast(Contract.id, String).ilike(search_pattern),
            ]
            if normalized_query.isdigit():
                search_conditions.append(Contract.id == int(normalized_query))
            search_filters.append(or_(*search_conditions))

        view_filter = self._contract_view_filter(view)
        filters = [*search_filters]
        if view_filter is not None:
            filters.append(view_filter)

        count_stmt = select(func.count()).select_from(Contract)
        if filters:
            count_stmt = count_stmt.where(*filters)
        total = int((await self.db.execute(count_stmt)).scalar_one())

        count_values: dict[str, int] = {}
        for count_view in ("active", "completed", "cancelled", "disputed"):
            count_filter = self._contract_view_filter(count_view)
            count_filters = [*search_filters]
            if count_filter is not None:
                count_filters.append(count_filter)
            view_count_stmt = select(func.count()).select_from(Contract)
            if count_filters:
                view_count_stmt = view_count_stmt.where(*count_filters)
            count_values[count_view] = int(
                (await self.db.execute(view_count_stmt)).scalar_one()
            )

        items_stmt = select(Contract).options(
            selectinload(Contract.payment_plan).selectinload(PaymentPlan.milestones),
            selectinload(Contract.rfq),
        )
        if filters:
            items_stmt = items_stmt.where(*filters)
        items_result = await self.db.execute(
            items_stmt.order_by(Contract.created_at.desc(), Contract.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = items_result.scalars().unique().all()
        actor_ids = [c.buyer_actor_id for c in items] + [c.supplier_actor_id for c in items]
        parties = await self._load_catalog_owners(actor_ids)

        return {
            "items": [
                self._serialize_contract_list_item(
                    item,
                    parties.get(item.buyer_actor_id),
                    parties.get(item.supplier_actor_id),
                )
                for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size) if total else 1,
            "view_counts": count_values,
        }

    async def get_contract_detail(self, contract_id: int) -> dict:
        result = await self.db.execute(
            select(Contract)
            .where(Contract.id == contract_id)
            .options(
                selectinload(Contract.payment_plan).selectinload(PaymentPlan.milestones),
                selectinload(Contract.files),
                selectinload(Contract.conversation).selectinload(Conversation.messages),
                selectinload(Contract.rfq),
                selectinload(Contract.proposal),
            )
        )
        contract = result.scalar_one_or_none()
        if not contract:
            raise NotFoundError("Contract not found")

        parties = await self._load_catalog_owners(
            [contract.buyer_actor_id, contract.supplier_actor_id]
        )
        buyer = parties.get(contract.buyer_actor_id)
        supplier = parties.get(contract.supplier_actor_id)

        history_result = await self.db.execute(
            select(AuditLog)
            .where(
                AuditLog.resource_type == "contract",
                AuditLog.resource_id == str(contract.id),
            )
            .options(selectinload(AuditLog.user))
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(50)
        )
        history = history_result.scalars().all()

        messages = []
        if contract.conversation:
            for message in contract.conversation.messages:
                messages.append(
                    {
                        "id": message.id,
                        "sender_id": message.sender_id,
                        "text": message.text,
                        "created_at": message.created_at,
                    }
                )
        messages.sort(key=lambda item: item["created_at"], reverse=True)

        milestones = (
            list(contract.payment_plan.milestones) if contract.payment_plan else []
        )
        escrow = self._escrow_summary(milestones, contract.currency.value)

        return {
            **self._serialize_contract_list_item(contract, buyer, supplier),
            "description": contract.description,
            "start_date": contract.start_date,
            "due_date": contract.due_date,
            "payment_type": contract.payment_type.value,
            "buyer": buyer,
            "supplier": supplier,
            "rfq": (
                {
                    "id": contract.rfq.id,
                    "title": contract.rfq.title,
                    "status": contract.rfq.status.value,
                }
                if contract.rfq
                else None
            ),
            "proposal": (
                {
                    "id": contract.proposal.id,
                    "price": contract.proposal.price,
                    "status": contract.proposal.status.value,
                }
                if contract.proposal
                else None
            ),
            "payment_plan": (
                {
                    "id": contract.payment_plan.id,
                    "payment_type": contract.payment_plan.payment_type.value,
                }
                if contract.payment_plan
                else None
            ),
            "milestones": [
                {
                    "id": milestone.id,
                    "title": milestone.title,
                    "percentage": milestone.percentage,
                    "amount": milestone.amount,
                    "trigger": milestone.trigger,
                    "status": milestone.status.value,
                }
                for milestone in sorted(milestones, key=lambda value: value.id)
            ],
            "files": [
                {
                    "id": file.id,
                    "file_name": file.file_name,
                    "file_url": public_file_url(file.file_url),
                    "file_type": file.file_type,
                    "uploaded_by": file.uploaded_by,
                    "created_at": file.created_at,
                }
                for file in sorted(
                    contract.files, key=lambda value: value.created_at, reverse=True
                )
            ],
            "messages": messages,
            "escrow": escrow,
            "history": [
                {
                    "id": entry.id,
                    "action": entry.action,
                    "details": entry.details or {},
                    "created_at": entry.created_at,
                    "actor": (
                        {
                            "id": entry.user.id,
                            "email": entry.user.email,
                            "name": f"{entry.user.first_name} {entry.user.last_name}".strip(),
                        }
                        if entry.user
                        else None
                    ),
                }
                for entry in history
            ],
        }

    async def apply_contract_action(
        self,
        contract_id: int,
        action: str,
        current_user: User,
        reason: str | None = None,
    ) -> dict:
        result = await self.db.execute(
            select(Contract)
            .where(Contract.id == contract_id)
            .options(
                selectinload(Contract.payment_plan).selectinload(PaymentPlan.milestones),
            )
        )
        contract = result.scalar_one_or_none()
        if not contract:
            raise NotFoundError("Contract not found")

        allowed = {"freeze", "cancel", "force_complete", "open_investigation"}
        if action not in allowed:
            raise ValidationError("Unsupported contract action")
        if action == "force_complete" and current_user.role == UserRole.moderator:
            raise ForbiddenError("Moderator cannot force-complete contracts")
        if action in {"cancel", "force_complete", "open_investigation"} and not (
            reason and reason.strip()
        ):
            raise ValidationError("Reason is required for this action")

        previous_status = contract.status.value
        milestones = (
            list(contract.payment_plan.milestones) if contract.payment_plan else []
        )
        freeze_statuses = {
            PaymentMilestoneStatus.funded,
            PaymentMilestoneStatus.in_progress,
            PaymentMilestoneStatus.submitted,
            PaymentMilestoneStatus.approved,
            PaymentMilestoneStatus.awaiting_payment,
        }

        if action == "freeze":
            for milestone in milestones:
                if milestone.status in freeze_statuses:
                    milestone.status = PaymentMilestoneStatus.disputed
        elif action == "cancel":
            if contract.status in {ContractStatus.completed, ContractStatus.cancelled}:
                raise ConflictError("Contract is already closed")
            contract.status = ContractStatus.cancelled
        elif action == "force_complete":
            if contract.status == ContractStatus.completed:
                raise ConflictError("Contract is already completed")
            contract.status = ContractStatus.completed
            for milestone in milestones:
                if milestone.status == PaymentMilestoneStatus.funded:
                    milestone.status = PaymentMilestoneStatus.released
        elif action == "open_investigation":
            if contract.status in {ContractStatus.completed, ContractStatus.cancelled}:
                raise ConflictError("Cannot open investigation on a closed contract")
            active = await self.db.execute(
                select(Dispute).where(
                    Dispute.contract_id == contract.id,
                    Dispute.status.in_(
                        [
                            DisputeStatus.open,
                            DisputeStatus.under_review,
                            DisputeStatus.appealed,
                        ]
                    ),
                )
            )
            if active.scalar_one_or_none():
                raise ConflictError("An active dispute already exists for this contract")
            had_resolved = (
                await self.db.execute(
                    select(Dispute.id)
                    .where(
                        Dispute.contract_id == contract.id,
                        Dispute.status == DisputeStatus.resolved,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            dispute = Dispute(
                contract_id=contract.id,
                status=(
                    DisputeStatus.appealed if had_resolved else DisputeStatus.open
                ),
                opened_by_actor_id=None,
                buyer_statement=reason.strip() if reason else None,
            )
            self.db.add(dispute)
            contract.status = ContractStatus.disputed
            await self.db.flush()
            await log_audit(
                self.db,
                user_id=current_user.id,
                action="dispute.open",
                resource_type="dispute",
                resource_id=str(dispute.id),
                details={
                    "contract_id": contract.id,
                    "status": dispute.status.value,
                    "reason": reason.strip() if reason else None,
                    "source": "admin.open_investigation",
                },
            )

        for actor_id in (contract.buyer_actor_id, contract.supplier_actor_id):
            user_id = await self._resolve_catalog_owner_user_id(actor_id)
            if not user_id:
                continue
            title, body = self._contract_action_notification(
                action, contract.title, reason
            )
            self.db.add(
                Notification(
                    user_id=user_id,
                    company_id=None,
                    type=NotificationType.system,
                    title=title,
                    body=body,
                    href=f"/customer/contracts/{contract.id}",
                )
            )

        await log_audit(
            self.db,
            user_id=current_user.id,
            action=f"admin.contract.{action}",
            resource_type="contract",
            resource_id=str(contract.id),
            details={
                "reason": reason.strip() if reason else None,
                "previous_status": previous_status,
                "status": contract.status.value,
            },
        )
        await self.db.flush()
        return {
            "id": contract.id,
            "action": action,
            "status": contract.status.value,
        }

    @staticmethod
    def _contract_view_filter(view: str):
        if view == "active":
            return Contract.status.in_(
                [
                    ContractStatus.pending_payment,
                    ContractStatus.active,
                    ContractStatus.delivered,
                ]
            )
        if view == "completed":
            return Contract.status == ContractStatus.completed
        if view == "cancelled":
            return Contract.status == ContractStatus.cancelled
        if view == "disputed":
            return Contract.status == ContractStatus.disputed
        return None

    @staticmethod
    def _escrow_summary(milestones: list, currency: str) -> dict:
        held_statuses = {
            PaymentMilestoneStatus.funded,
            PaymentMilestoneStatus.submitted,
            PaymentMilestoneStatus.in_progress,
            PaymentMilestoneStatus.awaiting_payment,
        }
        held = released = disputed = 0.0
        for milestone in milestones:
            if milestone.status == PaymentMilestoneStatus.released:
                released += milestone.amount
            elif milestone.status == PaymentMilestoneStatus.disputed:
                disputed += milestone.amount
            elif milestone.status in held_statuses:
                held += milestone.amount
        return {
            "held": held,
            "released": released,
            "disputed": disputed,
            "currency": currency,
        }

    def _serialize_contract_list_item(
        self,
        contract: Contract,
        buyer: dict | None,
        supplier: dict | None,
    ) -> dict:
        milestones = (
            list(contract.payment_plan.milestones)
            if getattr(contract, "payment_plan", None) and contract.payment_plan
            else []
        )
        escrow = self._escrow_summary(milestones, contract.currency.value)
        return {
            "id": contract.id,
            "title": contract.title,
            "status": contract.status.value,
            "agreed_amount": contract.agreed_amount,
            "currency": contract.currency.value,
            "payment_type": contract.payment_type.value,
            "rfq_id": contract.rfq_id,
            "proposal_id": contract.proposal_id,
            "buyer": buyer,
            "supplier": supplier,
            "escrow_held": escrow["held"],
            "created_at": contract.created_at,
        }

    @staticmethod
    def _contract_action_notification(
        action: str, contract_title: str, reason: str | None
    ) -> tuple[str, str]:
        messages = {
            "freeze": (
                "Escrow заморожен",
                f"Администратор заморозил средства по контракту «{contract_title}».",
            ),
            "cancel": (
                "Контракт отменён",
                f"Контракт «{contract_title}» отменён администратором.",
            ),
            "force_complete": (
                "Контракт принудительно завершён",
                f"Контракт «{contract_title}» принудительно завершён администратором.",
            ),
            "open_investigation": (
                "Открыто расследование",
                f"По контракту «{contract_title}» открыто административное расследование.",
            ),
        }
        title, body = messages[action]
        if reason:
            body = f"{body} Причина: {reason.strip()}"
        return title, body

    async def list_finance(
        self,
        page: int = 1,
        page_size: int = 20,
        view: str = "platform_revenue",
        query: str | None = None,
    ) -> dict:
        search_filters: list = []
        normalized_query = query.strip() if query else ""
        if normalized_query:
            search_pattern = f"%{normalized_query}%"
            search_conditions = [
                PlatformPayment.title.ilike(search_pattern),
                PlatformPayment.description.ilike(search_pattern),
                PlatformPayment.external_id.ilike(search_pattern),
                cast(PlatformPayment.id, String).ilike(search_pattern),
            ]
            if normalized_query.isdigit():
                search_conditions.append(PlatformPayment.id == int(normalized_query))
            search_filters.append(or_(*search_conditions))

        view_filter = self._finance_view_filter(view)
        filters = [*search_filters]
        if view_filter is not None:
            filters.append(view_filter)

        count_stmt = select(func.count()).select_from(PlatformPayment)
        if filters:
            count_stmt = count_stmt.where(*filters)
        total = int((await self.db.execute(count_stmt)).scalar_one())

        count_values: dict[str, int] = {}
        for count_view in (
            "platform_revenue",
            "subscriptions",
            "commission",
            "refunds",
            "payouts",
        ):
            count_filter = self._finance_view_filter(count_view)
            count_filters = [*search_filters]
            if count_filter is not None:
                count_filters.append(count_filter)
            view_count_stmt = select(func.count()).select_from(PlatformPayment)
            if count_filters:
                view_count_stmt = view_count_stmt.where(*count_filters)
            count_values[count_view] = int(
                (await self.db.execute(view_count_stmt)).scalar_one()
            )

        items_stmt = select(PlatformPayment).options(
            selectinload(PlatformPayment.invoice),
        )
        if filters:
            items_stmt = items_stmt.where(*filters)
        items_result = await self.db.execute(
            items_stmt.order_by(
                PlatformPayment.created_at.desc(), PlatformPayment.id.desc()
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = items_result.scalars().unique().all()
        actor_ids = [item.actor_id for item in items if item.actor_id]
        parties = await self._load_catalog_owners(actor_ids)

        return {
            "items": [
                self._serialize_finance_list_item(item, parties.get(item.actor_id))
                for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size) if total else 1,
            "view_counts": count_values,
        }

    async def get_finance_detail(self, payment_id: int) -> dict:
        result = await self.db.execute(
            select(PlatformPayment)
            .where(PlatformPayment.id == payment_id)
            .options(
                selectinload(PlatformPayment.invoice),
                selectinload(PlatformPayment.withdrawal),
                selectinload(PlatformPayment.contract),
            )
        )
        payment = result.scalar_one_or_none()
        if not payment:
            raise NotFoundError("Payment not found")

        parties = await self._load_catalog_owners(
            [payment.actor_id] if payment.actor_id else []
        )
        history_result = await self.db.execute(
            select(AuditLog)
            .where(
                AuditLog.resource_type == "platform_payment",
                AuditLog.resource_id == str(payment.id),
            )
            .options(selectinload(AuditLog.user))
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(50)
        )
        history = history_result.scalars().all()

        return {
            **self._serialize_finance_list_item(
                payment, parties.get(payment.actor_id) if payment.actor_id else None
            ),
            "description": payment.description,
            "external_id": payment.external_id,
            "paid_at": payment.paid_at,
            "failed_at": payment.failed_at,
            "refunded_at": payment.refunded_at,
            "metadata": payment.metadata_json or {},
            "actor": parties.get(payment.actor_id) if payment.actor_id else None,
            "invoice": (
                {
                    "id": payment.invoice.id,
                    "number": payment.invoice.number,
                    "title": payment.invoice.title,
                    "status": payment.invoice.status.value,
                    "amount": payment.invoice.amount,
                    "currency": payment.invoice.currency,
                }
                if payment.invoice
                else None
            ),
            "withdrawal": (
                {
                    "id": payment.withdrawal.id,
                    "amount": payment.withdrawal.amount,
                    "status": payment.withdrawal.status.value,
                    "currency": payment.withdrawal.currency,
                }
                if payment.withdrawal
                else None
            ),
            "contract": (
                {
                    "id": payment.contract.id,
                    "title": payment.contract.title,
                    "status": payment.contract.status.value,
                }
                if payment.contract
                else None
            ),
            "subscription_user_id": payment.subscription_user_id,
            "history": [
                {
                    "id": entry.id,
                    "action": entry.action,
                    "details": entry.details or {},
                    "created_at": entry.created_at,
                    "actor": (
                        {
                            "id": entry.user.id,
                            "email": entry.user.email,
                            "name": f"{entry.user.first_name} {entry.user.last_name}".strip(),
                        }
                        if entry.user
                        else None
                    ),
                }
                for entry in history
            ],
        }

    async def export_finance_csv(
        self,
        view: str = "platform_revenue",
        query: str | None = None,
        payment_id: int | None = None,
    ) -> str:
        if payment_id is not None:
            detail = await self.get_finance_detail(payment_id)
            rows = [detail]
        else:
            data = await self.list_finance(
                page=1, page_size=5000, view=view, query=query
            )
            rows = data["items"]

        import csv
        import io

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "id",
                "type",
                "status",
                "gateway",
                "amount",
                "commission",
                "currency",
                "title",
                "invoice_id",
                "created_at",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.get("id"),
                    row.get("type"),
                    row.get("status"),
                    row.get("gateway"),
                    row.get("amount"),
                    row.get("commission"),
                    row.get("currency"),
                    row.get("title"),
                    row.get("invoice_id"),
                    row.get("created_at"),
                ]
            )
        return buffer.getvalue()

    async def apply_finance_action(
        self,
        payment_id: int,
        action: str,
        current_user: User,
        reason: str | None = None,
    ) -> dict:
        result = await self.db.execute(
            select(PlatformPayment)
            .where(PlatformPayment.id == payment_id)
            .options(
                selectinload(PlatformPayment.invoice),
                selectinload(PlatformPayment.withdrawal),
            )
        )
        payment = result.scalar_one_or_none()
        if not payment:
            raise NotFoundError("Payment not found")

        allowed = {"refund", "retry", "mark_paid"}
        if action not in allowed:
            raise ValidationError("Unsupported finance action")
        if action in {"refund", "retry"} and not (reason and reason.strip()):
            raise ValidationError("Reason is required for this action")

        previous_status = payment.status.value
        now = datetime.now(timezone.utc)

        if action == "mark_paid":
            if payment.status not in {
                PlatformPaymentStatus.pending,
                PlatformPaymentStatus.processing,
            }:
                raise ConflictError(
                    "Only pending/processing payments can be marked paid"
                )
            payment.status = PlatformPaymentStatus.paid
            payment.paid_at = now
            if payment.invoice and payment.invoice.status != InvoiceStatus.paid:
                payment.invoice.status = InvoiceStatus.paid
                payment.invoice.paid_at = now
            if payment.withdrawal and payment.withdrawal.status in {
                WithdrawalStatus.pending,
                WithdrawalStatus.processing,
                WithdrawalStatus.failed,
            }:
                payment.withdrawal.status = WithdrawalStatus.completed
                payment.withdrawal.completed_at = now
        elif action == "retry":
            if payment.status != PlatformPaymentStatus.failed:
                raise ConflictError("Only failed payments can be retried")
            payment.status = PlatformPaymentStatus.processing
            payment.failed_at = None
            await self.db.flush()
            payment.status = PlatformPaymentStatus.paid
            payment.paid_at = now
            if payment.invoice:
                payment.invoice.status = InvoiceStatus.paid
                payment.invoice.paid_at = now
            if payment.withdrawal:
                payment.withdrawal.status = WithdrawalStatus.completed
                payment.withdrawal.completed_at = now
        elif action == "refund":
            if payment.status != PlatformPaymentStatus.paid:
                raise ConflictError("Only paid payments can be refunded")
            if payment.type == PlatformPaymentType.refund:
                raise ConflictError("Refund payments cannot be refunded")
            payment.status = PlatformPaymentStatus.refunded
            payment.refunded_at = now
            if payment.invoice:
                payment.invoice.status = InvoiceStatus.cancelled
            if (
                payment.withdrawal
                and payment.withdrawal.status != WithdrawalStatus.cancelled
            ):
                payment.withdrawal.status = WithdrawalStatus.cancelled

        if payment.actor_id:
            user_id = await self._resolve_catalog_owner_user_id(payment.actor_id)
            if user_id:
                title, body = self._finance_action_notification(
                    action, payment.title, reason
                )
                self.db.add(
                    Notification(
                        user_id=user_id,
                        company_id=None,
                        type=NotificationType.system,
                        title=title,
                        body=body,
                        href="/supplier/finance",
                    )
                )

        await log_audit(
            self.db,
            user_id=current_user.id,
            action=f"admin.finance.{action}",
            resource_type="platform_payment",
            resource_id=str(payment.id),
            details={
                "reason": reason.strip() if reason else None,
                "previous_status": previous_status,
                "status": payment.status.value,
            },
        )
        await self.db.flush()
        return {
            "id": payment.id,
            "action": action,
            "status": payment.status.value,
            "type": payment.type.value,
        }

    @staticmethod
    def _finance_view_filter(view: str):
        mapping = {
            "platform_revenue": PlatformPaymentType.platform_revenue,
            "subscriptions": PlatformPaymentType.subscription,
            "commission": PlatformPaymentType.commission,
            "refunds": PlatformPaymentType.refund,
            "payouts": PlatformPaymentType.payout,
        }
        payment_type = mapping.get(view)
        if payment_type is None:
            return None
        return PlatformPayment.type == payment_type

    def _serialize_finance_list_item(
        self,
        payment: PlatformPayment,
        actor: dict | None,
    ) -> dict:
        return {
            "id": payment.id,
            "type": payment.type.value,
            "status": payment.status.value,
            "gateway": payment.gateway.value,
            "amount": payment.amount,
            "commission": payment.commission,
            "currency": payment.currency,
            "title": payment.title,
            "invoice_id": payment.invoice_id,
            "withdrawal_id": payment.withdrawal_id,
            "contract_id": payment.contract_id,
            "actor": actor,
            "created_at": payment.created_at,
            "updated_at": payment.updated_at,
        }

    @staticmethod
    def _finance_action_notification(
        action: str, payment_title: str, reason: str | None
    ) -> tuple[str, str]:
        messages = {
            "mark_paid": (
                "Платёж отмечен оплаченным",
                f"Платёж «{payment_title}» отмечен как оплаченный.",
            ),
            "retry": (
                "Повтор платежа",
                f"Повторная обработка платежа «{payment_title}» выполнена.",
            ),
            "refund": (
                "Платёж возвращён",
                f"По платежу «{payment_title}» выполнен возврат.",
            ),
        }
        title, body = messages[action]
        if reason:
            body = f"{body} Причина: {reason.strip()}"
        return title, body

    REPORT_REASONS = ("spam", "fraud", "counterfeit", "abuse", "other")
    REPORT_TARGET_TYPES = ("catalog", "rfq", "proposal")

    async def list_reports(
        self,
        page: int = 1,
        page_size: int = 20,
        view: str = "all",
        query: str | None = None,
    ) -> dict:
        if view not in ("all", *self.REPORT_REASONS):
            raise ValidationError("Unsupported report view")

        union_stmt = self._reports_union_stmt(view=view, query=query).subquery()
        total = int(
            (
                await self.db.execute(select(func.count()).select_from(union_stmt))
            ).scalar_one()
        )
        pages = max(1, (total + page_size - 1) // page_size) if total else 1
        page = min(max(1, page), pages)

        rows = (
            await self.db.execute(
                select(union_stmt)
                .order_by(union_stmt.c.created_at.desc(), union_stmt.c.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).mappings().all()

        items = []
        for row in rows:
            items.append(await self._serialize_report_list_item(row))

        view_counts = {"all": 0}
        for reason in self.REPORT_REASONS:
            count_stmt = self._reports_union_stmt(view=reason, query=query).subquery()
            count = int(
                (
                    await self.db.execute(select(func.count()).select_from(count_stmt))
                ).scalar_one()
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

    async def get_report_detail(self, target_type: str, report_id: int) -> dict:
        report, target = await self._load_report_bundle(target_type, report_id)
        reporter = report.reporter
        owner = await self._resolve_reported_owner(target_type, target)
        evidence = self._build_report_evidence(target_type, report, target)
        history = await self._load_report_history(target_type, target, report.id)

        return {
            "id": report.id,
            "target_type": target_type,
            "reason": report.reason.value,
            "status": report.status.value,
            "details": report.details,
            "created_at": report.created_at,
            "resolved_at": report.resolved_at,
            "reporter": {
                "id": reporter.id,
                "email": reporter.email,
                "name": f"{reporter.first_name} {reporter.last_name}".strip(),
            },
            "reported_object": self._serialize_reported_object(
                target_type, target, owner
            ),
            "owner": owner,
            "evidence": evidence,
            "history": history,
        }

    async def apply_report_action(
        self,
        target_type: str,
        report_id: int,
        action: str,
        current_user: User,
        reason: str | None = None,
    ) -> dict:
        allowed = {"dismiss", "warn", "suspend", "delete"}
        if action not in allowed:
            raise ValidationError("Unsupported report action")
        if action in {"suspend", "delete"} and current_user.role == UserRole.moderator:
            raise ForbiddenError("Moderator cannot perform this action")
        if action in {"warn", "suspend", "delete"} and not (reason and reason.strip()):
            raise ValidationError("Reason is required for this action")

        report, target = await self._load_report_bundle(target_type, report_id)
        if report.status != self._report_open_status(target_type):
            raise ConflictError("Report is already closed")

        owner = await self._resolve_reported_object_owner(target_type, target)
        object_title = self._reported_object_title(target_type, target)
        object_id = self._reported_object_id(target_type, target)
        now = datetime.now(timezone.utc)

        if action == "dismiss":
            report.status = self._report_dismissed_status(target_type)
            report.resolved_at = now
            report.resolved_by_id = current_user.id
        elif action == "warn":
            report.status = self._report_resolved_status(target_type)
            report.resolved_at = now
            report.resolved_by_id = current_user.id
            owner_user_id = owner.get("user_id") if owner else None
            if owner_user_id:
                title, body = self._report_action_notification(
                    action, object_title, reason
                )
                self.db.add(
                    Notification(
                        user_id=owner_user_id,
                        company_id=None,
                        type=NotificationType.system,
                        title=title,
                        body=body,
                        href=self._owner_href(target_type, object_id),
                    )
                )
        elif action == "suspend":
            report.status = self._report_resolved_status(target_type)
            report.resolved_at = now
            report.resolved_by_id = current_user.id
            await self._suspend_reported_owner(owner, current_user, reason)
            owner_user_id = owner.get("user_id") if owner else None
            if owner_user_id:
                title, body = self._report_action_notification(
                    action, object_title, reason
                )
                self.db.add(
                    Notification(
                        user_id=owner_user_id,
                        company_id=None,
                        type=NotificationType.system,
                        title=title,
                        body=body,
                        href=self._owner_href(target_type, object_id),
                    )
                )
        elif action == "delete":
            await log_audit(
                self.db,
                user_id=current_user.id,
                action="admin.report.delete",
                resource_type="report",
                resource_id=f"{target_type}:{report.id}",
                details={
                    "reason": reason.strip() if reason else None,
                    "target_type": target_type,
                    "report_id": report.id,
                    "object_id": object_id,
                    "report_reason": report.reason.value,
                    "status": "resolved",
                },
            )
            await log_audit(
                self.db,
                user_id=current_user.id,
                action="admin.report.delete",
                resource_type=self._object_resource_type(target_type),
                resource_id=str(object_id),
                details={
                    "reason": reason.strip() if reason else None,
                    "report_id": report.id,
                    "target_type": target_type,
                },
            )
            owner_user_id = owner.get("user_id") if owner else None
            if owner_user_id:
                title, body = self._report_action_notification(
                    action, object_title, reason
                )
                self.db.add(
                    Notification(
                        user_id=owner_user_id,
                        company_id=None,
                        type=NotificationType.system,
                        title=title,
                        body=body,
                        href=self._owner_href(target_type, object_id),
                    )
                )
            report.status = self._report_resolved_status(target_type)
            report.resolved_at = now
            report.resolved_by_id = current_user.id
            await self.db.flush()
            await self._delete_reported_object(
                target_type, target, current_user, reason
            )
            await self.db.flush()
            return {
                "id": report_id,
                "target_type": target_type,
                "action": action,
                "status": "resolved",
            }

        await log_audit(
            self.db,
            user_id=current_user.id,
            action=f"admin.report.{action}",
            resource_type="report",
            resource_id=f"{target_type}:{report.id}",
            details={
                "reason": reason.strip() if reason else None,
                "target_type": target_type,
                "report_id": report.id,
                "object_id": object_id,
                "report_reason": report.reason.value,
                "status": report.status.value,
            },
        )
        await log_audit(
            self.db,
            user_id=current_user.id,
            action=f"admin.report.{action}",
            resource_type=self._object_resource_type(target_type),
            resource_id=str(object_id),
            details={
                "reason": reason.strip() if reason else None,
                "report_id": report.id,
                "target_type": target_type,
            },
        )
        await self.db.flush()
        return {
            "id": report.id,
            "target_type": target_type,
            "action": action,
            "status": report.status.value,
        }

    def _reports_union_stmt(self, view: str, query: str | None):
        reason_filter = None if view == "all" else view
        normalized_query = query.strip() if query else ""
        search_pattern = f"%{normalized_query}%" if normalized_query else None

        catalog_filters = [CatalogItemReport.status == CatalogItemReportStatus.open]
        if reason_filter:
            catalog_filters.append(
                CatalogItemReport.reason == CatalogItemReportReason(reason_filter)
            )

        rfq_filters = [RfqReport.status == RfqReportStatus.open]
        if reason_filter:
            rfq_filters.append(RfqReport.reason == RfqReportReason(reason_filter))

        proposal_filters = [ProposalReport.status == ProposalReportStatus.open]
        if reason_filter:
            proposal_filters.append(
                ProposalReport.reason == ProposalReportReason(reason_filter)
            )

        catalog_q = (
            select(
                CatalogItemReport.id.label("id"),
                literal("catalog").label("target_type"),
                cast(CatalogItemReport.reason, String).label("reason"),
                CatalogItemReport.details.label("details"),
                cast(CatalogItemReport.status, String).label("status"),
                CatalogItemReport.created_at.label("created_at"),
                CatalogItemReport.reporter_user_id.label("reporter_user_id"),
                cast(CatalogItemReport.item_id, String).label("object_id"),
            )
            .join(CatalogItem, CatalogItem.id == CatalogItemReport.item_id)
            .join(User, User.id == CatalogItemReport.reporter_user_id)
            .where(*catalog_filters)
        )
        rfq_q = (
            select(
                RfqReport.id.label("id"),
                literal("rfq").label("target_type"),
                cast(RfqReport.reason, String).label("reason"),
                RfqReport.details.label("details"),
                cast(RfqReport.status, String).label("status"),
                RfqReport.created_at.label("created_at"),
                RfqReport.reporter_user_id.label("reporter_user_id"),
                RfqReport.rfq_id.label("object_id"),
            )
            .join(Rfq, Rfq.id == RfqReport.rfq_id)
            .join(User, User.id == RfqReport.reporter_user_id)
            .where(*rfq_filters)
        )
        proposal_q = (
            select(
                ProposalReport.id.label("id"),
                literal("proposal").label("target_type"),
                cast(ProposalReport.reason, String).label("reason"),
                ProposalReport.details.label("details"),
                cast(ProposalReport.status, String).label("status"),
                ProposalReport.created_at.label("created_at"),
                ProposalReport.reporter_user_id.label("reporter_user_id"),
                cast(ProposalReport.proposal_id, String).label("object_id"),
            )
            .join(Proposal, Proposal.id == ProposalReport.proposal_id)
            .join(User, User.id == ProposalReport.reporter_user_id)
            .where(*proposal_filters)
        )

        if search_pattern:
            catalog_q = catalog_q.where(
                or_(
                    CatalogItem.title.ilike(search_pattern),
                    CatalogItemReport.details.ilike(search_pattern),
                    User.email.ilike(search_pattern),
                    User.first_name.ilike(search_pattern),
                    User.last_name.ilike(search_pattern),
                    cast(CatalogItemReport.id, String).ilike(search_pattern),
                )
            )
            rfq_q = rfq_q.where(
                or_(
                    Rfq.title.ilike(search_pattern),
                    RfqReport.details.ilike(search_pattern),
                    User.email.ilike(search_pattern),
                    User.first_name.ilike(search_pattern),
                    User.last_name.ilike(search_pattern),
                    cast(RfqReport.id, String).ilike(search_pattern),
                    RfqReport.rfq_id.ilike(search_pattern),
                )
            )
            proposal_q = proposal_q.where(
                or_(
                    Proposal.message.ilike(search_pattern),
                    ProposalReport.details.ilike(search_pattern),
                    User.email.ilike(search_pattern),
                    User.first_name.ilike(search_pattern),
                    User.last_name.ilike(search_pattern),
                    cast(ProposalReport.id, String).ilike(search_pattern),
                    cast(ProposalReport.proposal_id, String).ilike(search_pattern),
                )
            )

        return union_all(catalog_q, rfq_q, proposal_q)

    async def _serialize_report_list_item(self, row) -> dict:
        reporter = (
            await self.db.execute(select(User).where(User.id == row["reporter_user_id"]))
        ).scalar_one_or_none()
        target_type = row["target_type"]
        object_id = row["object_id"]
        title = "—"
        href = "/"
        if target_type == "catalog":
            item = (
                await self.db.execute(
                    select(CatalogItem).where(CatalogItem.id == int(object_id))
                )
            ).scalar_one_or_none()
            if item:
                title = item.title
                href = f"/admin/catalog/{item.id}"
        elif target_type == "rfq":
            rfq = (
                await self.db.execute(select(Rfq).where(Rfq.id == object_id))
            ).scalar_one_or_none()
            if rfq:
                title = rfq.title
                href = f"/admin/rfqs/{rfq.id}"
        elif target_type == "proposal":
            proposal = (
                await self.db.execute(
                    select(Proposal).where(Proposal.id == int(object_id))
                )
            ).scalar_one_or_none()
            if proposal:
                title = (proposal.message or f"Предложение #{proposal.id}")[:120]
                href = f"/admin/proposals/{proposal.id}"

        details = row["details"] or ""
        return {
            "id": row["id"],
            "target_type": target_type,
            "reason": row["reason"],
            "status": row["status"],
            "details_preview": details[:160] if details else None,
            "created_at": row["created_at"],
            "reporter": {
                "id": reporter.id if reporter else 0,
                "email": reporter.email if reporter else "",
                "name": (
                    f"{reporter.first_name} {reporter.last_name}".strip()
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

    async def _load_report_bundle(self, target_type: str, report_id: int):
        if target_type not in self.REPORT_TARGET_TYPES:
            raise ValidationError("Unsupported report target type")
        if target_type == "catalog":
            result = await self.db.execute(
                select(CatalogItemReport)
                .where(CatalogItemReport.id == report_id)
                .options(
                    selectinload(CatalogItemReport.reporter),
                    selectinload(CatalogItemReport.item).selectinload(CatalogItem.media),
                )
            )
            report = result.scalar_one_or_none()
            if not report or not report.item:
                raise NotFoundError("Report not found")
            return report, report.item
        if target_type == "rfq":
            result = await self.db.execute(
                select(RfqReport)
                .where(RfqReport.id == report_id)
                .options(
                    selectinload(RfqReport.reporter),
                    selectinload(RfqReport.rfq).selectinload(Rfq.attachments),
                )
            )
            report = result.scalar_one_or_none()
            if not report or not report.rfq:
                raise NotFoundError("Report not found")
            return report, report.rfq
        result = await self.db.execute(
            select(ProposalReport)
            .where(ProposalReport.id == report_id)
            .options(
                selectinload(ProposalReport.reporter),
                selectinload(ProposalReport.proposal).selectinload(Proposal.attachment),
                selectinload(ProposalReport.proposal).selectinload(Proposal.contract),
            )
        )
        report = result.scalar_one_or_none()
        if not report or not report.proposal:
            raise NotFoundError("Report not found")
        return report, report.proposal

    async def _resolve_reported_owner(self, target_type: str, target) -> dict | None:
        return await self._resolve_reported_object_owner(target_type, target)

    async def _resolve_reported_object_owner(
        self, target_type: str, target
    ) -> dict | None:
        if target_type == "catalog":
            owners = await self._load_catalog_owners([target.actor_id])
            return owners.get(target.actor_id)
        if target_type == "rfq":
            owners = await self._load_catalog_owners([target.actor_id])
            return owners.get(target.actor_id)
        owners = await self._load_catalog_owners([target.supplier_actor_id])
        return owners.get(target.supplier_actor_id)

    def _serialize_reported_object(
        self, target_type: str, target, owner: dict | None
    ) -> dict:
        object_id = self._reported_object_id(target_type, target)
        return {
            "type": target_type,
            "id": str(object_id),
            "title": self._reported_object_title(target_type, target),
            "href": self._admin_href(target_type, object_id),
            "status": getattr(getattr(target, "status", None), "value", None),
            "owner": owner,
        }

    @staticmethod
    def _reported_object_id(target_type: str, target):
        if target_type == "catalog":
            return target.id
        if target_type == "rfq":
            return target.id
        return target.id

    @staticmethod
    def _reported_object_title(target_type: str, target) -> str:
        if target_type == "catalog":
            return target.title
        if target_type == "rfq":
            return target.title
        return (target.message or f"Предложение #{target.id}")[:200]

    @staticmethod
    def _admin_href(target_type: str, object_id) -> str:
        if target_type == "catalog":
            return f"/admin/catalog/{object_id}"
        if target_type == "rfq":
            return f"/admin/rfqs/{object_id}"
        return f"/admin/proposals/{object_id}"

    @staticmethod
    def _owner_href(target_type: str, object_id) -> str:
        if target_type == "catalog":
            return f"/supplier/catalog/{object_id}"
        if target_type == "rfq":
            return f"/customer/rfqs/{object_id}"
        return f"/supplier/proposals/{object_id}"

    @staticmethod
    def _object_resource_type(target_type: str) -> str:
        if target_type == "catalog":
            return "catalog_item"
        if target_type == "rfq":
            return "rfq"
        return "proposal"

    @staticmethod
    def _report_open_status(target_type: str):
        if target_type == "catalog":
            return CatalogItemReportStatus.open
        if target_type == "rfq":
            return RfqReportStatus.open
        return ProposalReportStatus.open

    @staticmethod
    def _report_dismissed_status(target_type: str):
        if target_type == "catalog":
            return CatalogItemReportStatus.dismissed
        if target_type == "rfq":
            return RfqReportStatus.dismissed
        return ProposalReportStatus.dismissed

    @staticmethod
    def _report_resolved_status(target_type: str):
        if target_type == "catalog":
            return CatalogItemReportStatus.resolved
        if target_type == "rfq":
            return RfqReportStatus.resolved
        return ProposalReportStatus.resolved

    def _build_report_evidence(self, target_type: str, report, target) -> dict:
        files: list[dict] = []
        if target_type == "catalog":
            for media in sorted(target.media or [], key=lambda m: m.sort_order):
                files.append(
                    {
                        "file_name": media.file_name,
                        "file_url": public_file_url(media.file_url),
                        "file_type": media.media_type.value,
                    }
                )
        elif target_type == "rfq":
            for attachment in target.attachments or []:
                files.append(
                    {
                        "file_name": attachment.file_name,
                        "file_url": public_file_url(attachment.file_url),
                        "file_type": attachment.file_type,
                    }
                )
        elif target_type == "proposal" and target.attachment:
            files.append(
                {
                    "file_name": target.attachment.file_name,
                    "file_url": public_file_url(target.attachment.file_url),
                    "file_type": target.attachment.file_type,
                }
            )
        return {"details": report.details, "files": files}

    async def _load_report_history(
        self, target_type: str, target, report_id: int
    ) -> list[dict]:
        object_id = str(self._reported_object_id(target_type, target))
        resource_type = self._object_resource_type(target_type)
        result = await self.db.execute(
            select(AuditLog)
            .where(
                or_(
                    (
                        (AuditLog.resource_type == resource_type)
                        & (AuditLog.resource_id == object_id)
                    ),
                    (
                        (AuditLog.resource_type == "report")
                        & (AuditLog.resource_id == f"{target_type}:{report_id}")
                    ),
                )
            )
            .options(selectinload(AuditLog.user))
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        )
        history = result.scalars().unique().all()
        return [
            {
                "id": entry.id,
                "action": entry.action,
                "details": entry.details or {},
                "created_at": entry.created_at,
                "actor": (
                    {
                        "id": entry.user.id,
                        "email": entry.user.email,
                        "name": f"{entry.user.first_name} {entry.user.last_name}".strip(),
                    }
                    if entry.user
                    else None
                ),
            }
            for entry in history
        ]

    async def _suspend_reported_owner(
        self, owner: dict | None, current_user: User, reason: str | None
    ) -> None:
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

    async def _delete_reported_object(
        self,
        target_type: str,
        target,
        current_user: User,
        reason: str | None,
    ) -> None:
        if target_type == "catalog":
            await self.apply_catalog_action(
                item_id=target.id,
                action="delete",
                current_user=current_user,
                reason=reason,
            )
            return
        if target_type == "rfq":
            await self.apply_rfq_action(
                rfq_id=target.id,
                action="delete",
                current_user=current_user,
                reason=reason,
            )
            return
        await self.apply_proposal_action(
            proposal_id=target.id,
            action="delete",
            current_user=current_user,
            reason=reason,
        )

    @staticmethod
    def _report_action_notification(
        action: str, object_title: str, reason: str | None
    ) -> tuple[str, str]:
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
        if reason:
            body = f"{body} Причина: {reason.strip()}"
        return title, body
