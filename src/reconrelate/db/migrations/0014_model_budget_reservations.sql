CREATE TABLE model_budget_reservations (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  model TEXT NOT NULL,
  domain TEXT NOT NULL,
  input_tokens INTEGER NOT NULL CHECK(input_tokens >= 0),
  output_tokens INTEGER NOT NULL CHECK(output_tokens >= 0),
  cloud_tokens INTEGER NOT NULL CHECK(cloud_tokens >= 0),
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE INDEX idx_model_budget_reservations_run
  ON model_budget_reservations(run_id, created_at);
