ALTER TABLE observations ADD COLUMN data_policy_version TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE observations ADD COLUMN cache_allowed INTEGER NOT NULL DEFAULT 1 CHECK(cache_allowed IN (0, 1));
ALTER TABLE observations ADD COLUMN export_scope TEXT NOT NULL DEFAULT 'normalized'
  CHECK(export_scope IN ('none', 'derived_only', 'normalized'));
ALTER TABLE observations ADD COLUMN raw_retention TEXT NOT NULL DEFAULT 'hash_only'
  CHECK(raw_retention IN ('none', 'hash_only'));
