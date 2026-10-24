ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS totp_secret text,
    ADD COLUMN IF NOT EXISTS totp_enabled boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS totp_backup_codes_hash text[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN public.users.totp_secret IS 'Fernet-encrypted TOTP shared secret';
COMMENT ON COLUMN public.users.totp_enabled IS 'Whether TOTP 2FA is confirmed and required at login';
COMMENT ON COLUMN public.users.totp_backup_codes_hash IS 'bcrypt hashes of one-time backup codes';
