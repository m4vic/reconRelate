ALTER TABLE domain_cache
  ADD COLUMN observations_json TEXT NOT NULL DEFAULT '[]';
