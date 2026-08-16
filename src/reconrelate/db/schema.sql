PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  root_domain TEXT NOT NULL,
  status TEXT NOT NULL,
  max_depth INTEGER NOT NULL,
  pivot_top_k INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  error_message TEXT
);

CREATE TABLE IF NOT EXISTS nodes (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  node_type TEXT NOT NULL,
  value_norm TEXT NOT NULL,
  value_hash TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  UNIQUE(run_id, node_type, value_norm),
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS edges (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  from_node_id TEXT NOT NULL,
  to_node_id TEXT NOT NULL,
  relation_type TEXT NOT NULL,
  depth INTEGER NOT NULL,
  source TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0.0,
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id),
  FOREIGN KEY(from_node_id) REFERENCES nodes(id),
  FOREIGN KEY(to_node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS lineage (
  run_id TEXT NOT NULL,
  child_node_id TEXT NOT NULL,
  parent_node_id TEXT NOT NULL,
  depth INTEGER NOT NULL,
  UNIQUE(run_id, child_node_id, parent_node_id),
  FOREIGN KEY(run_id) REFERENCES runs(id),
  FOREIGN KEY(child_node_id) REFERENCES nodes(id),
  FOREIGN KEY(parent_node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS pivot_decisions (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  domain_node_id TEXT NOT NULL,
  identifier_value_norm TEXT NOT NULL,
  identifier_type TEXT NOT NULL,
  score REAL NOT NULL,
  reason_short TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id),
  FOREIGN KEY(domain_node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS processed_domains (
  run_id TEXT NOT NULL,
  domain_node_id TEXT NOT NULL,
  depth INTEGER NOT NULL,
  UNIQUE(run_id, domain_node_id),
  FOREIGN KEY(run_id) REFERENCES runs(id),
  FOREIGN KEY(domain_node_id) REFERENCES nodes(id)
);

-- Cross-run scrape cache: what mapping a domain produced, so a later run can replay
-- an already-mapped subtree instead of re-scraping it ("only scrape what's not scraped").
-- Keyed by domain (NOT run_id) — this is deliberately shared across all runs.
CREATE TABLE IF NOT EXISTS domain_cache (
  domain TEXT PRIMARY KEY,
  last_scraped TEXT NOT NULL,
  children_json TEXT NOT NULL DEFAULT '[]'
);

-- Performance indexes for common queries
CREATE INDEX IF NOT EXISTS idx_nodes_run_type ON nodes(run_id, node_type);
CREATE INDEX IF NOT EXISTS idx_edges_run ON edges(run_id);
CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_node_id);
CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_node_id);
CREATE INDEX IF NOT EXISTS idx_processed_run ON processed_domains(run_id);
