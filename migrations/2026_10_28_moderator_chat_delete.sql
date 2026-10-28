-- Soft-delete for deal / proposal chat messages (moderator tools)

ALTER TABLE messages ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS deleted_by_user_id INTEGER REFERENCES users(id);

ALTER TABLE proposal_messages ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE proposal_messages ADD COLUMN IF NOT EXISTS deleted_by_user_id INTEGER REFERENCES users(id);
