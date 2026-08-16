CREATE TABLE provider_state (
  provider TEXT PRIMARY KEY,
  consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK(consecutive_failures >= 0),
  circuit_open_until TEXT,
  last_error_class TEXT,
  last_error_message TEXT,
  updated_at TEXT NOT NULL
);

CREATE INDEX idx_provider_state_open ON provider_state(circuit_open_until);
