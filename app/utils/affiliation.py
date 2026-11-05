"""Who is writing: the sender's company and position, for chats and reviews.

In a B2B deal people speak for their companies, so every message shows the
person, their role in the company and the company itself. The SQL helpers
below add two columns, ``sender_company`` and ``sender_role``, to a message
query. They look for the sender among the members of the companies that take
part in the conversation, so a person who belongs to several companies is shown
with the one that is actually in this deal.
"""

# Actor ids taking part in each kind of conversation, for a message aliased "m".
DIRECT_CHAT_ACTORS = (
    "SELECT v.x FROM direct_conversations dc "
    "CROSS JOIN LATERAL (VALUES (dc.buyer_actor_id), (dc.supplier_actor_id)) v(x) "
    "WHERE dc.id = m.conversation_id"
)
CONTRACT_CHAT_ACTORS = (
    "SELECT v.x FROM conversations cv JOIN contracts ct ON ct.id = cv.contract_id "
    "CROSS JOIN LATERAL (VALUES (ct.buyer_actor_id), (ct.supplier_actor_id)) v(x) "
    "WHERE cv.id = m.conversation_id"
)
PROPOSAL_CHAT_ACTORS = (
    "SELECT v.x FROM proposals pp JOIN rfqs rr ON rr.id = pp.rfq_id "
    "CROSS JOIN LATERAL (VALUES (rr.actor_id), (pp.supplier_actor_id)) v(x) "
    "WHERE pp.id = m.proposal_id"
)


def sender_affiliation_columns(sender_expr: str, actors_sql: str) -> str:
    """Select-list SQL for ``sender_company`` and ``sender_role``."""
    source = (
        "FROM actors sa "
        "JOIN companies sc ON sc.id = sa.company_id "
        f"JOIN company_users scu ON scu.company_id = sc.id AND scu.user_id = {sender_expr} "
        f"WHERE sa.kind::text = 'company' AND sa.id IN ({actors_sql}) "
        "ORDER BY sa.id LIMIT 1"
    )
    return (
        f"(SELECT COALESCE(NULLIF(sc.title, ''), sc.legal_name) {source}) AS sender_company, "
        f"(SELECT scu.role::text {source}) AS sender_role"
    )


CONTRACT_MESSAGE_AFFILIATION = sender_affiliation_columns("m.sender_id", CONTRACT_CHAT_ACTORS)
PROPOSAL_MESSAGE_AFFILIATION = sender_affiliation_columns("m.sender_id", PROPOSAL_CHAT_ACTORS)


def affiliation_fields(row) -> dict:
    """The two affiliation values from a row, or None when the query has no such columns."""
    keys = row.keys() if hasattr(row, "keys") else ()
    return {
        "sender_company": row["sender_company"] if "sender_company" in keys else None,
        "sender_role": row["sender_role"] if "sender_role" in keys else None,
    }
