-- Outbound payment-gateway HTTP audit: request before send, response before processing

CREATE TABLE IF NOT EXISTS payment_gateway_http_logs (
    id SERIAL PRIMARY KEY,
    gateway VARCHAR(20) NOT NULL DEFAULT 'alif',
    direction VARCHAR(20) NOT NULL,
    operation VARCHAR(64) NOT NULL,
    order_id VARCHAR(64),
    method VARCHAR(10),
    url TEXT,
    request_headers JSONB,
    request_body JSONB,
    response_status INTEGER,
    response_body JSONB,
    response_raw TEXT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    responded_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_payment_gateway_http_logs_order_id
ON payment_gateway_http_logs (order_id);

CREATE INDEX IF NOT EXISTS ix_payment_gateway_http_logs_created_at
ON payment_gateway_http_logs (created_at DESC);

CREATE INDEX IF NOT EXISTS ix_payment_gateway_http_logs_operation
ON payment_gateway_http_logs (gateway, operation, created_at DESC);
