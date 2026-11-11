-- The journal keeps every request (views too), every page a person opens on the
-- site, and the device it came from.

-- What used to sit inside the JSON of a row gets columns of its own, so the
-- journal can be filtered and sorted without unpacking every row.
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS method VARCHAR(10);
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS path VARCHAR(500);
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS status_code SMALLINT;
-- A request: how long the server took. A page: how long the person stayed on it.
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS duration_ms INTEGER;
-- One id for everything written while a request was served (the request row and
-- the events it caused); for a page it is the id the browser gave that view.
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS request_id UUID;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS device_key VARCHAR(64);
-- One visit: a browser tab from opening to closing.
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS session_key VARCHAR(36);
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS country VARCHAR(2);

-- Rows written before these columns: fill them once from the JSON. The index is
-- empty afterwards, so on later starts this finds nothing without reading the table.
CREATE INDEX IF NOT EXISTS audit_logs_http_unfilled_idx
    ON audit_logs (id)
    WHERE action LIKE 'http.%' AND path IS NULL;

UPDATE audit_logs
SET path = LEFT(details->>'path', 500),
    method = UPPER(split_part(action, '.', 2)),
    status_code = CASE
        WHEN details->>'status_code' ~ '^[0-9]{3}$' THEN (details->>'status_code')::smallint
    END
WHERE action LIKE 'http.%' AND path IS NULL AND details->>'path' IS NOT NULL;

CREATE INDEX IF NOT EXISTS audit_logs_device_created_idx
    ON audit_logs (device_key, created_at DESC)
    WHERE device_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS audit_logs_ip_created_idx
    ON audit_logs (ip_address, created_at DESC)
    WHERE ip_address IS NOT NULL;

CREATE INDEX IF NOT EXISTS audit_logs_request_idx
    ON audit_logs (request_id)
    WHERE request_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS audit_logs_page_created_idx
    ON audit_logs (created_at DESC, id DESC)
    WHERE action LIKE 'page.%';

-- Views outnumber events many times over; events keep a short index of their own.
CREATE INDEX IF NOT EXISTS audit_logs_event_created_idx
    ON audit_logs (created_at DESC, id DESC)
    WHERE action NOT LIKE 'http.%' AND action NOT LIKE 'page.%';

-- Every device the platform has seen. The key is the id a browser makes for
-- itself; a client that sends none is told apart by its address and headers,
-- and its key starts with «h-».
CREATE TABLE IF NOT EXISTS client_devices (
    device_key VARCHAR(64) PRIMARY KEY,
    user_agent VARCHAR(500),
    os_name VARCHAR(40),
    os_version VARCHAR(40),
    browser_name VARCHAR(60),
    browser_version VARCHAR(40),
    device_type VARCHAR(20) NOT NULL DEFAULT 'other',
    device_model VARCHAR(80),
    is_bot BOOLEAN NOT NULL DEFAULT FALSE,
    language VARCHAR(35),
    timezone VARCHAR(64),
    screen VARCHAR(40),
    -- What the browser told about itself (screen, memory, graphics card, ...).
    client JSONB,
    reported_at TIMESTAMPTZ,
    -- A hash of the machine's own traits: the same machine under a new id.
    fingerprint VARCHAR(64),
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    first_ip VARCHAR(50),
    last_ip VARCHAR(50),
    last_country VARCHAR(2),
    last_user_id INTEGER,
    requests_count BIGINT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS client_devices_last_seen_idx ON client_devices (last_seen_at DESC);
CREATE INDEX IF NOT EXISTS client_devices_fingerprint_idx
    ON client_devices (fingerprint)
    WHERE fingerprint IS NOT NULL;

-- Who used a device, and from which addresses.
CREATE TABLE IF NOT EXISTS client_device_users (
    device_key VARCHAR(64) NOT NULL,
    user_id INTEGER NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_ip VARCHAR(50),
    requests_count BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (device_key, user_id)
);

CREATE INDEX IF NOT EXISTS client_device_users_user_idx
    ON client_device_users (user_id, last_seen_at DESC);

CREATE TABLE IF NOT EXISTS client_device_ips (
    device_key VARCHAR(64) NOT NULL,
    ip_address VARCHAR(50) NOT NULL,
    country VARCHAR(2),
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    requests_count BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (device_key, ip_address)
);

CREATE INDEX IF NOT EXISTS client_device_ips_ip_idx ON client_device_ips (ip_address);
