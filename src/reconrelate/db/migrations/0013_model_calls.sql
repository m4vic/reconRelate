CREATE TABLE model_calls (
  id TEXT PRIMARY KEY,
  run_id TEXT,
  domain TEXT NOT NULL,
  model TEXT NOT NULL,
  task TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  cloud INTEGER NOT NULL CHECK(cloud IN (0, 1)),
  status TEXT NOT NULL CHECK(status IN ('success', 'error', 'budget_exceeded')),
  reserved_input_tokens INTEGER NOT NULL CHECK(reserved_input_tokens >= 0),
  reserved_output_tokens INTEGER NOT NULL CHECK(reserved_output_tokens >= 0),
  reserved_cloud_tokens INTEGER NOT NULL CHECK(reserved_cloud_tokens >= 0),
  actual_input_tokens INTEGER CHECK(actual_input_tokens IS NULL OR actual_input_tokens >= 0),
  actual_output_tokens INTEGER CHECK(actual_output_tokens IS NULL OR actual_output_tokens >= 0),
  actual_total_tokens INTEGER CHECK(actual_total_tokens IS NULL OR actual_total_tokens >= 0),
  provider_reported_cost_usd REAL CHECK(provider_reported_cost_usd IS NULL OR provider_reported_cost_usd >= 0),
  latency_ms INTEGER NOT NULL CHECK(latency_ms >= 0),
  error_class TEXT,
  error_message TEXT,
  started_at TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE INDEX idx_model_calls_run ON model_calls(run_id, started_at);
CREATE INDEX idx_model_calls_model ON model_calls(model, status, started_at);
