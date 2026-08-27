import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from src.core.security import hash_password
from src.db.session import AsyncSessionLocal
from src.models import (
    ActorType,
    Category,
    Company,
    CompanyProfile,
    CompanyRole,
    CompanyStats,
    CompanyUser,
    User,
    UserRole,
    UserStatus,
    VerificationStatus,
)
from src.modules.actors.service import ActorService

DEMO_ADMIN_EMAIL = "admin@example.com"
DEMO_BUYER_EMAIL = "buyer@example.com"
DEMO_SUPPLIER_EMAIL = "supplier@example.com"
DEMO_BOTH_EMAIL = "both@example.com"

LEGACY_EMAIL_MAP = {
    "admin@brandmarket.local": DEMO_ADMIN_EMAIL,
    "buyer@demo.local": DEMO_BUYER_EMAIL,
    "supplier@demo.local": DEMO_SUPPLIER_EMAIL,
}

CATEGORIES = [
    ("ИТ и разработка", "it", None),
    ("Веб-разработка", "web", "it"),
    ("Мобильные приложения", "mobile", "it"),
    ("Маркетинг и реклама", "marketing", None),
    ("SEO продвижение", "seo", "marketing"),
    ("Логистика и склад", "logistics", None),
    ("FTL перевозки", "ftl", "logistics"),
    ("Строительство и ремонт", "construction", None),
    ("Ремонт помещений", "renovation", "construction"),
]


async def migrate_legacy_emails(db) -> None:
    for old_email, new_email in LEGACY_EMAIL_MAP.items():
        result = await db.execute(select(User).where(User.email == old_email))
        user = result.scalar_one_or_none()
        if not user:
            continue
        conflict = await db.execute(select(User).where(User.email == new_email))
        if conflict.scalar_one_or_none():
            continue
        user.email = new_email
        print(f"Migrated {old_email} -> {new_email}")


async def get_user_by_emails(db, *emails: str) -> User | None:
    for email in emails:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user:
            return user
    return None


async def backfill_actors(db) -> None:
    from src.models import Actor, ActorKind

    actor_svc = ActorService(db)
    users = (await db.execute(select(User))).scalars().all()
    for user in users:
        await actor_svc.ensure_individual_actors_for_user(user)

    companies = (await db.execute(select(Company))).scalars().all()
    for company in companies:
        existing = await db.execute(
            select(Actor).where(Actor.company_id == company.id, Actor.kind == ActorKind.company)
        )
        if existing.scalars().first():
            continue
        await actor_svc.ensure_company_actor(company, company.actor_type)


async def seed() -> None:
    from src.db.schema import prepare_database

    await prepare_database()

    async with AsyncSessionLocal() as db:
        await migrate_legacy_emails(db)

        existing = await db.execute(select(Category).limit(1))
        if not existing.scalar_one_or_none():
            slug_to_id: dict[str, int] = {}
            for name, slug, parent_slug in CATEGORIES:
                parent_id = slug_to_id.get(parent_slug) if parent_slug else None
                cat = Category(parent_id=parent_id, name=name, slug=slug)
                db.add(cat)
                await db.flush()
                slug_to_id[slug] = cat.id
            print(f"Seeded {len(CATEGORIES)} categories")

        if not await get_user_by_emails(db, DEMO_ADMIN_EMAIL, "admin@brandmarket.local"):
            db.add(
                User(
                    email=DEMO_ADMIN_EMAIL,
                    password_hash=hash_password("Admin123!"),
                    first_name="Platform",
                    last_name="Admin",
                    role=UserRole.admin,
                    status=UserStatus.active,
                )
            )
            await db.flush()
            print(f"Seeded admin: {DEMO_ADMIN_EMAIL} / Admin123!")

        buyer_user = await get_user_by_emails(db, DEMO_BUYER_EMAIL, "buyer@demo.local")
        if not buyer_user:
            buyer_user = User(
                email=DEMO_BUYER_EMAIL,
                password_hash=hash_password("Buyer123!"),
                first_name="Иван",
                last_name="Заказчик",
                role=UserRole.buyer,
                status=UserStatus.active,
            )
            db.add(buyer_user)
            await db.flush()
            actor_svc = ActorService(db)
            await actor_svc.ensure_individual_actor(buyer_user, ActorType.buyer)
            company = Company(
                title="ООО Демо Заказчик",
                actor_type=ActorType.buyer,
                owner_id=buyer_user.id,
                legal_name="ООО Демо Заказчик",
                country="Таджикистан",
                city="Душанбе",
                verification_status=VerificationStatus.verified,
                rating=0.0,
            )
            db.add(company)
            await db.flush()
            db.add(CompanyProfile(company_id=company.id, languages=["ru"], industries=["IT"]))
            db.add(CompanyStats(company_id=company.id))
            db.add(
                CompanyUser(
                    company_id=company.id, user_id=buyer_user.id, role=CompanyRole.director
                )
            )
            await actor_svc.ensure_company_actor(company, ActorType.buyer)
            print(f"Seeded buyer: {DEMO_BUYER_EMAIL} / Buyer123! (company id={company.id})")

        supplier_user = await get_user_by_emails(db, DEMO_SUPPLIER_EMAIL, "supplier@demo.local")
        if not supplier_user:
            supplier_user = User(
                email=DEMO_SUPPLIER_EMAIL,
                password_hash=hash_password("Supplier123!"),
                first_name="Пётр",
                last_name="Исполнитель",
                role=UserRole.supplier,
                status=UserStatus.active,
            )
            db.add(supplier_user)
            await db.flush()
            actor_svc = ActorService(db)
            await actor_svc.ensure_individual_actor(supplier_user, ActorType.supplier)
            company = Company(
                title="ТехноСнаб",
                actor_type=ActorType.supplier,
                owner_id=supplier_user.id,
                legal_name="ООО «ТехноСнаб»",
                country="Таджикистан",
                city="Душанбе",
                description="Исполнитель IT-оборудования",
                verification_status=VerificationStatus.verified,
                rating=4.7,
            )
            db.add(company)
            await db.flush()
            db.add(
                CompanyProfile(
                    company_id=company.id,
                    founded_year=2015,
                    employees_count=45,
                    languages=["ru", "en"],
                    industries=["IT"],
                )
            )
            db.add(
                CompanyStats(
                    company_id=company.id,
                    completed_contracts=28,
                    active_contracts=3,
                    average_rating=4.7,
                )
            )
            db.add(
                CompanyUser(
                    company_id=company.id, user_id=supplier_user.id, role=CompanyRole.director
                )
            )
            await actor_svc.ensure_company_actor(company, ActorType.supplier)
            print(f"Seeded supplier: {DEMO_SUPPLIER_EMAIL} / Supplier123! (company id={company.id})")

        both_user = await get_user_by_emails(db, DEMO_BOTH_EMAIL)
        if not both_user:
            both_user = User(
                email=DEMO_BOTH_EMAIL,
                password_hash=hash_password("Both123!"),
                first_name="Алекс",
                last_name="Универсал",
                role=UserRole.both,
                status=UserStatus.active,
            )
            db.add(both_user)
            await db.flush()
            actor_svc = ActorService(db)
            await actor_svc.ensure_individual_actors_for_user(both_user)
            buyer_co = Company(
                title="ООО Универсал Закупки",
                actor_type=ActorType.buyer,
                owner_id=both_user.id,
                country="Таджикистан",
                city="Душанбе",
                verification_status=VerificationStatus.verified,
            )
            db.add(buyer_co)
            await db.flush()
            db.add(CompanyStats(company_id=buyer_co.id))
            db.add(
                CompanyUser(company_id=buyer_co.id, user_id=both_user.id, role=CompanyRole.director)
            )
            await actor_svc.ensure_company_actor(buyer_co, ActorType.buyer)
            supplier_co = Company(
                title="ООО Универсал Поставки",
                actor_type=ActorType.supplier,
                owner_id=both_user.id,
                country="Таджикистан",
                city="Душанбе",
                verification_status=VerificationStatus.pending,
            )
            db.add(supplier_co)
            await db.flush()
            db.add(CompanyStats(company_id=supplier_co.id))
            db.add(
                CompanyUser(
                    company_id=supplier_co.id, user_id=both_user.id, role=CompanyRole.director
                )
            )
            await actor_svc.ensure_company_actor(supplier_co, ActorType.supplier)
            print(f"Seeded both: {DEMO_BOTH_EMAIL} / Both123!")

        await backfill_actors(db)
        await db.commit()
        print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(seed())
