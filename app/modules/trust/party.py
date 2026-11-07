"""One reliability mark per owner, on every profile they run.

A party is everything one user runs: their own buying and selling profiles and
every company they own, whether a company buys, sells or both. Its mark is
computed from all those profiles together — completed deals, reviews about
them, deals without a dispute the party lost — and an admin-set mark applies to
the whole party, so it shows on the buying and the selling profile alike.
"""

from __future__ import annotations

from app.modules.trust.levels import TIER_LABELS_RU, resolve_reputation_tier

ACTIVE_DISPUTE = "d.status::text IN ('open', 'under_review', 'appealed')"


def party_owner_sql(a: str) -> str:
    """The user behind actor alias `a`: the company owner, or the person."""
    return f"COALESCE((SELECT oc.owner_id FROM companies oc WHERE oc.id = {a}.company_id), {a}.user_id)"


def party_actor_ids_sql(a: str) -> str:
    """Ids of every actor of the same party as actor alias `a` (always includes `a`)."""
    return f"""(
        SELECT pa.id FROM actors pa
        LEFT JOIN companies pc ON pc.id = pa.company_id
        WHERE pa.id = {a}.id
           OR ({a}.company_id IS NOT NULL AND pa.company_id = {a}.company_id)
           OR COALESCE(pc.owner_id, pa.user_id) = {party_owner_sql(a)}
    )"""


def _lost_dispute_sql(ids: str) -> str:
    """A deal counts against the party when a dispute is open or went against its side."""
    return f"""(
        (ct.supplier_actor_id = ANY({ids}) AND (ct.status::text = 'disputed' OR EXISTS (
            SELECT 1 FROM disputes d WHERE d.contract_id = ct.id
              AND ({ACTIVE_DISPUTE} OR d.resolution::text IN ('refund_buyer', 'partial_refund'))
        )))
        OR (ct.buyer_actor_id = ANY({ids}) AND (ct.status::text = 'disputed' OR EXISTS (
            SELECT 1 FROM disputes d WHERE d.contract_id = ct.id
              AND ({ACTIVE_DISPUTE} OR d.resolution::text = 'release_funds')
        )))
    )"""


def party_reputation_columns(a: str) -> str:
    """SELECT columns with the party's combined record, for actor alias `a`."""
    # The party's ids are collected once per row, then matched with = ANY.
    party = f"(SELECT ARRAY{party_actor_ids_sql(a)} AS ids) p"
    deals = f"{party} JOIN contracts ct ON (ct.supplier_actor_id = ANY(p.ids) OR ct.buyer_actor_id = ANY(p.ids))"
    lost = _lost_dispute_sql("p.ids")
    return f"""
        (SELECT COUNT(*) FROM {deals} WHERE ct.status::text = 'completed') AS party_completed,
        (SELECT COUNT(*) FROM {deals} WHERE {lost}) AS party_disputed,
        (SELECT COUNT(*) FROM {deals}
         WHERE ct.status::text = 'completed' AND NOT {lost}) AS party_clean_completed,
        (SELECT COUNT(*) FROM {party} JOIN reviews r ON r.target_actor_id = ANY(p.ids)) AS party_reviews,
        (SELECT COALESCE(AVG(r.rating)::float, 0) FROM {party}
         JOIN reviews r ON r.target_actor_id = ANY(p.ids)) AS party_rating,
        (SELECT pa.reputation_override FROM actors pa
         WHERE pa.id IN {party_actor_ids_sql(a)} AND pa.reputation_override IS NOT NULL
         ORDER BY pa.reputation_override_at DESC NULLS LAST, pa.id
         LIMIT 1) AS party_override
    """


def _get(row, key, default=None):
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def party_tier(row, verification_status: str | None, *, fallback_rating: float = 0.0) -> str | None:
    """The party's mark from a row with party_* columns; None when they are missing."""
    if _get(row, "party_completed") is None:
        return None
    override = _get(row, "party_override")
    if override in TIER_LABELS_RU:
        return override
    completed = int(_get(row, "party_completed") or 0)
    disputed = int(_get(row, "party_disputed") or 0)
    clean = int(_get(row, "party_clean_completed") or 0)
    reviews = int(_get(row, "party_reviews") or 0)
    rating = float(_get(row, "party_rating") or 0) if reviews else float(fallback_rating or 0)
    total = clean + disputed
    success = round(clean / total * 100, 1) if total > 0 else None
    return resolve_reputation_tier(
        verification_status=verification_status,
        completed_contracts=completed,
        rating=rating,
        success_rate=success,
    )
