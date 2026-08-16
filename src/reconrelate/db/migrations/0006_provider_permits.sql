CREATE TABLE provider_rate_windows (
  provider TEXT NOT NULL,
  window_started_at TEXT NOT NULL,
  request_count INTEGER NOT NULL DEFAULT 0 CHECK(request_count >= 0),
  updated_at TEXT NOT NULL,
  PRIMARY KEY(provider, window_started_at)
);

CREATE TABLE provider_concurrency_leases (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  owner TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX idx_provider_leases_active
  ON provider_concurrency_leases(provider, expires_at);
CREATE INDEX idx_provider_rate_windows_age
  ON provider_rate_windows(window_started_at);
