CREATE TABLE auth_sessions (
    token_hash CHAR(64) PRIMARY KEY,
    username TEXT NOT NULL,
    credential_tag CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ
);
CREATE INDEX auth_sessions_expiry ON auth_sessions (expires_at);
CREATE TABLE auth_login_limits (
    client_hash CHAR(64) PRIMARY KEY,
    window_started_at TIMESTAMPTZ NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0)
);
