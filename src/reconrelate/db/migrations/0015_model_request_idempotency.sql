ALTER TABLE model_budget_reservations ADD COLUMN request_key TEXT;
CREATE UNIQUE INDEX idx_model_budget_reservations_request_key
  ON model_budget_reservations(request_key) WHERE request_key IS NOT NULL;

ALTER TABLE model_calls ADD COLUMN request_key TEXT;
ALTER TABLE model_calls ADD COLUMN result_json TEXT;
CREATE INDEX idx_model_calls_request_key ON model_calls(request_key, status);
