ALTER TABLE pivot_decisions ADD COLUMN evidence_gap TEXT NOT NULL DEFAULT 'asset_discovery';
ALTER TABLE pivot_decisions ADD COLUMN utility REAL NOT NULL DEFAULT 0;
ALTER TABLE pivot_decisions ADD COLUMN estimated_logical_calls INTEGER NOT NULL DEFAULT 1;
ALTER TABLE pivot_decisions ADD COLUMN policy_version TEXT NOT NULL DEFAULT 'legacy';
