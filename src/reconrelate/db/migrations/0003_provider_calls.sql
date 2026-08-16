CREATE TABLE provider_calls (
  id TEXT PRIMARY KEY,
  run_id TEXT,
  provider TEXT NOT NULL,
  capability TEXT NOT NULL,
  operation TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('success', 'empty', 'timeout', 'rate_limited', 'auth_error', 'malformed', 'error', 'circuit_open')),
  attempts INTEGER NOT NULL CHECK(attempts >= 0),
  latency_ms INTEGER NOT NULL CHECK(latency_ms >= 0),
  billable INTEGER NOT NULL DEFAULT 0 CHECK(billable IN (0, 1)),
  units REAL NOT NULL DEFAULT 0 CHECK(units >= 0),
  error_class TEXT,
  error_message TEXT,
  started_at TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE INDEX idx_provider_calls_run ON provider_calls(run_id, started_at);
CREATE INDEX idx_provider_calls_provider ON provider_calls(provider, status, started_at);
