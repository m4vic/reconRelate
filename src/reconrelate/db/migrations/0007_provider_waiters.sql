CREATE TABLE provider_waiters (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  owner TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE INDEX idx_provider_waiters_fifo
  ON provider_waiters(provider, created_at, id);
CREATE INDEX idx_provider_waiters_expiry
  ON provider_waiters(expires_at);
