ALTER TABLE provider_calls ADD COLUMN upstream_requests INTEGER NOT NULL DEFAULT 0
  CHECK(upstream_requests >= 0);
ALTER TABLE provider_calls ADD COLUMN pages INTEGER NOT NULL DEFAULT 0
  CHECK(pages >= 0);
