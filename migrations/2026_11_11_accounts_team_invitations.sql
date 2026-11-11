-- Who you are on the platform: company, private person, company employee.
-- Idempotent: this file runs on every start.

-- A team member who may look at everything and change nothing.
ALTER TYPE company_role ADD VALUE IF NOT EXISTS 'viewer';

-- An invitation to a company team. The person joins only after accepting it.
-- The link carries a random token; only its hash is stored.
CREATE TABLE IF NOT EXISTS company_invitations (
    id SERIAL PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'moderator', 'accountant', 'viewer')),
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'accepted', 'declined', 'cancelled')),
    invited_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    accepted_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    responded_at TIMESTAMPTZ
);

-- One open invitation per address in a company.
CREATE UNIQUE INDEX IF NOT EXISTS uq_company_invitations_pending
    ON company_invitations (company_id, lower(email))
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS ix_company_invitations_email
    ON company_invitations (lower(email))
    WHERE status = 'pending';

-- The public page of a private person (freelancer, craftsman): what they do,
-- skills, rate and portfolio. accepts_orders lists the person among suppliers;
-- company employees stay out of that list until they switch it on themselves.
CREATE TABLE IF NOT EXISTS individual_profiles (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    headline VARCHAR(120),
    about TEXT,
    skills TEXT[] NOT NULL DEFAULT '{}',
    city VARCHAR(100),
    website VARCHAR(255),
    rate_amount NUMERIC(14, 2),
    rate_currency VARCHAR(3) NOT NULL DEFAULT 'TJS',
    rate_unit VARCHAR(10) NOT NULL DEFAULT 'project'
        CHECK (rate_unit IN ('hour', 'project')),
    portfolio JSONB NOT NULL DEFAULT '[]'::jsonb,
    accepts_orders BOOLEAN NOT NULL DEFAULT FALSE,
    -- Where the cabinet opens after sign-in: in the person's company (they
    -- registered a company or joined one by invitation) or as themselves.
    prefers_company BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- People who registered before this change: those who already work as private
-- suppliers (no company, or their own offers, catalog or deals) stay listed.
INSERT INTO individual_profiles (user_id, accepts_orders)
SELECT
    u.id,
    EXISTS (
        SELECT 1 FROM actors a
        WHERE a.user_id = u.id AND a.kind::text = 'individual'
          AND a.side::text = 'supplier' AND a.is_active = TRUE
          AND (
              NOT EXISTS (SELECT 1 FROM company_users cu WHERE cu.user_id = u.id)
              OR EXISTS (SELECT 1 FROM proposals p WHERE p.supplier_actor_id = a.id)
              OR EXISTS (SELECT 1 FROM catalog_items ci WHERE ci.actor_id = a.id)
              OR EXISTS (SELECT 1 FROM contracts ct WHERE ct.supplier_actor_id = a.id)
          )
    )
FROM users u
WHERE u.role::text IN ('buyer', 'supplier', 'both')
ON CONFLICT (user_id) DO NOTHING;

-- The wide picture at the top of a company page.
ALTER TABLE companies ADD COLUMN IF NOT EXISTS cover TEXT;

-- The employee who wrote the request / the offer: shown to the other side as
-- the contact person next to the company name.
ALTER TABLE rfqs
    ADD COLUMN IF NOT EXISTS created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE proposals
    ADD COLUMN IF NOT EXISTS author_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;
