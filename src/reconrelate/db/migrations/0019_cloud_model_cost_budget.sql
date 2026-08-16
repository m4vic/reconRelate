ALTER TABLE runs ADD COLUMN max_cloud_cost_microusd INTEGER NOT NULL DEFAULT 0
  CHECK(max_cloud_cost_microusd >= 0);
ALTER TABLE runs ADD COLUMN model_price_catalog_version TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE model_budget_reservations ADD COLUMN cloud_cost_microusd INTEGER NOT NULL DEFAULT 0
  CHECK(cloud_cost_microusd >= 0);
ALTER TABLE model_calls ADD COLUMN reserved_cloud_cost_microusd INTEGER NOT NULL DEFAULT 0
  CHECK(reserved_cloud_cost_microusd >= 0);
ALTER TABLE model_calls ADD COLUMN price_catalog_version TEXT NOT NULL DEFAULT 'legacy';
