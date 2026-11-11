import enum


class UserRole(str, enum.Enum):
    buyer = "buyer"
    supplier = "supplier"
    both = "both"
    admin = "admin"
    superadmin = "superadmin"
    moderator = "moderator"


class UserStatus(str, enum.Enum):
    pending = "pending"
    active = "active"
    blocked = "blocked"


class CompanyRole(str, enum.Enum):
    director = "director"
    admin = "admin"
    moderator = "moderator"
    accountant = "accountant"
    viewer = "viewer"


class ActorType(str, enum.Enum):
    buyer = "buyer"
    supplier = "supplier"


class ActorKind(str, enum.Enum):
    individual = "individual"
    company = "company"


class TrustLevel(str, enum.Enum):
    basic = "basic"
    standard = "standard"
    verified = "verified"


class VerificationStatus(str, enum.Enum):
    pending = "pending"
    verified = "verified"
    rejected = "rejected"
    needs_documents = "needs_documents"


class CompanyOperationalStatus(str, enum.Enum):
    active = "active"
    blocked = "blocked"
    deactivated = "deactivated"


class RfqType(str, enum.Enum):
    product = "product"
    service = "service"


class BudgetType(str, enum.Enum):
    fixed = "fixed"
    range = "range"
    open = "open"


class RfqVisibility(str, enum.Enum):
    public = "public"
    invited_only = "invited_only"


class RfqStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    receiving_proposals = "receiving_proposals"
    supplier_selected = "supplier_selected"
    contract_created = "contract_created"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"
    expired = "expired"
    disputed = "disputed"
    archived = "archived"


class RfqReportReason(str, enum.Enum):
    spam = "spam"
    fraud = "fraud"
    counterfeit = "counterfeit"
    abuse = "abuse"
    other = "other"


class RfqReportStatus(str, enum.Enum):
    open = "open"
    resolved = "resolved"
    dismissed = "dismissed"


class Currency(str, enum.Enum):
    TJS = "TJS"
    USD = "USD"
    EUR = "EUR"
    KZT = "KZT"
    CNY = "CNY"


class ProposalStatus(str, enum.Enum):
    submitted = "submitted"
    viewed = "viewed"
    shortlisted = "shortlisted"
    pending_supplier = "pending_supplier"
    accepted = "accepted"
    rejected = "rejected"
    withdrawn = "withdrawn"
    archived = "archived"


class ProposalReportReason(str, enum.Enum):
    spam = "spam"
    fraud = "fraud"
    counterfeit = "counterfeit"
    abuse = "abuse"
    other = "other"


class ProposalReportStatus(str, enum.Enum):
    open = "open"
    resolved = "resolved"
    dismissed = "dismissed"


class PaymentType(str, enum.Enum):
    full_prepayment = "full_prepayment"
    split_payment = "split_payment"
    milestone = "milestone"
    full_postpayment = "full_postpayment"


class PaymentMode(str, enum.Enum):
    """Escrow holds the money in the bank until acceptance; direct pays the supplier at once."""

    escrow = "escrow"
    direct = "direct"


class ContractStatus(str, enum.Enum):
    pending_payment = "pending_payment"
    active = "active"
    delivered = "delivered"
    completed = "completed"
    cancelled = "cancelled"
    disputed = "disputed"


class PaymentMilestoneStatus(str, enum.Enum):
    pending = "pending"
    awaiting_payment = "awaiting_payment"
    funded = "funded"
    in_progress = "in_progress"
    submitted = "submitted"
    approved = "approved"
    released = "released"
    refunded = "refunded"
    disputed = "disputed"
    overdue = "overdue"
    cancelled = "cancelled"


class PaymentMilestoneTrigger(str, enum.Enum):
    contract_signed = "contract_signed"
    delivery_accepted = "delivery_accepted"


class MessageDeliveryStatus(str, enum.Enum):
    sent = "sent"
    delivered = "delivered"
    viewed = "viewed"


class DisputeStatus(str, enum.Enum):
    open = "open"
    under_review = "under_review"
    resolved = "resolved"
    appealed = "appealed"


class DisputeResolution(str, enum.Enum):
    release_funds = "release_funds"
    refund_buyer = "refund_buyer"
    partial_refund = "partial_refund"
    close_case = "close_case"


class WorkSubmissionStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"


class WorkSubmissionType(str, enum.Enum):
    delivery = "delivery"
    work = "work"


class NotificationType(str, enum.Enum):
    order = "order"
    offer = "offer"
    payment = "payment"
    system = "system"
    rfq = "rfq"
    contract = "contract"
    proposal = "proposal"


class CatalogItemType(str, enum.Enum):
    product = "product"
    service = "service"


class ItemStatus(str, enum.Enum):
    draft = "draft"
    pending_review = "pending_review"
    changes_requested = "changes_requested"
    active = "active"
    hidden = "hidden"
    archived = "archived"
    deleted = "deleted"


class CatalogItemReportReason(str, enum.Enum):
    spam = "spam"
    fraud = "fraud"
    counterfeit = "counterfeit"
    abuse = "abuse"
    other = "other"


class CatalogItemReportStatus(str, enum.Enum):
    open = "open"
    resolved = "resolved"
    dismissed = "dismissed"


class PricingType(str, enum.Enum):
    fixed = "fixed"
    tiered = "tiered"
    hourly = "hourly"
    monthly = "monthly"


class ItemMediaType(str, enum.Enum):
    image = "image"
    document = "document"
    video = "video"


class SupplierSubscriptionPlan(str, enum.Enum):
    none = "none"
    start = "start"
    pro = "pro"
    business = "business"


class WithdrawalDestinationType(str, enum.Enum):
    bank = "bank"
    wallet = "wallet"


class WithdrawalStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class InvoiceStatus(str, enum.Enum):
    draft = "draft"
    issued = "issued"
    paid = "paid"
    overdue = "overdue"
    cancelled = "cancelled"


class PlatformPaymentType(str, enum.Enum):
    platform_revenue = "platform_revenue"
    subscription = "subscription"
    commission = "commission"
    refund = "refund"
    payout = "payout"


class PlatformPaymentStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    paid = "paid"
    failed = "failed"
    refunded = "refunded"
    cancelled = "cancelled"


class PlatformPaymentGateway(str, enum.Enum):
    manual = "manual"
    mock = "mock"
    stripe = "stripe"
    yookassa = "yookassa"
    alif = "alif"
