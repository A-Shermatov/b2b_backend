-- Identity verification for individual (физлицо) buyers/suppliers.
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS individual_verification_status VARCHAR(20) NOT NULL DEFAULT 'none',
    ADD COLUMN IF NOT EXISTS individual_verification_reason TEXT,
    ADD COLUMN IF NOT EXISTS individual_verification_submitted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS individual_verified_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS individual_verified_by INTEGER REFERENCES users(id) ON DELETE SET NULL;

DO $$
BEGIN
    ALTER TABLE users ADD CONSTRAINT users_individual_verification_status_check
        CHECK (individual_verification_status IN ('none', 'pending', 'needs_documents', 'verified', 'rejected'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

CREATE INDEX IF NOT EXISTS ix_users_individual_verification_status
    ON users (individual_verification_status)
    WHERE individual_verification_status <> 'none';

CREATE TABLE IF NOT EXISTS user_documents (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    doc_type VARCHAR(20) NOT NULL DEFAULT 'other',
    file_name VARCHAR(255),
    file_url TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'uploaded',
    uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_user_documents_user ON user_documents (user_id, uploaded_at DESC);
