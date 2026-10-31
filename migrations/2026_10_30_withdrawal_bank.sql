-- Bank channel for supplier withdrawals (alif / dc / eskhata).
ALTER TABLE withdrawals
    ADD COLUMN IF NOT EXISTS bank VARCHAR(20);

UPDATE withdrawals SET bank = 'alif' WHERE bank IS NULL;

CREATE INDEX IF NOT EXISTS ix_withdrawals_bank ON withdrawals (bank);
