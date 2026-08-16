ALTER TABLE runs ADD COLUMN fast_model TEXT NOT NULL DEFAULT '';
ALTER TABLE runs ADD COLUMN model_routing_policy TEXT NOT NULL DEFAULT 'single-model-v1';
