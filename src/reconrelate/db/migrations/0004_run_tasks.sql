CREATE TABLE run_tasks (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  task_type TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pending', 'in_progress', 'succeeded', 'failed')),
  priority INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
  max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts >= 1),
  available_at TEXT NOT NULL,
  lease_until TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(run_id, idempotency_key),
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE INDEX idx_run_tasks_claim
  ON run_tasks(run_id, status, available_at, priority, created_at);
CREATE INDEX idx_run_tasks_lease
  ON run_tasks(status, lease_until);
