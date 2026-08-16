CREATE TABLE observations (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  dedup_key TEXT NOT NULL,
  subject_type TEXT NOT NULL,
  subject_value_norm TEXT NOT NULL,
  predicate TEXT NOT NULL,
  object_type TEXT,
  object_value_norm TEXT,
  source TEXT NOT NULL,
  source_record_id TEXT,
  observed_at TEXT NOT NULL,
  valid_from TEXT,
  valid_to TEXT,
  confidence REAL NOT NULL DEFAULT 0.0 CHECK(confidence >= 0.0 AND confidence <= 1.0),
  normalized_json TEXT NOT NULL DEFAULT '{}',
  raw_hash TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(run_id, dedup_key),
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE claims (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  claim_key TEXT NOT NULL,
  claim_type TEXT NOT NULL,
  subject_type TEXT NOT NULL,
  subject_value_norm TEXT NOT NULL,
  object_type TEXT NOT NULL,
  object_value_norm TEXT NOT NULL,
  status TEXT NOT NULL,
  confidence_class TEXT NOT NULL CHECK(confidence_class IN ('verified', 'probable', 'candidate', 'rejected')),
  score REAL NOT NULL CHECK(score >= 0.0 AND score <= 1.0),
  policy_version TEXT NOT NULL,
  valid_from TEXT,
  valid_to TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(run_id, claim_key),
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE claim_evidence (
  claim_id TEXT NOT NULL,
  observation_id TEXT NOT NULL,
  polarity TEXT NOT NULL CHECK(polarity IN ('supports', 'contradicts')),
  weight REAL NOT NULL CHECK(weight >= 0.0 AND weight <= 1.0),
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(claim_id, observation_id, polarity),
  FOREIGN KEY(claim_id) REFERENCES claims(id) ON DELETE CASCADE,
  FOREIGN KEY(observation_id) REFERENCES observations(id) ON DELETE CASCADE
);

CREATE TABLE source_lineage (
  child_source TEXT NOT NULL,
  parent_source TEXT NOT NULL,
  relationship TEXT NOT NULL DEFAULT 'derived_from',
  created_at TEXT NOT NULL,
  PRIMARY KEY(child_source, parent_source, relationship)
);

CREATE INDEX idx_observations_run_subject
  ON observations(run_id, subject_type, subject_value_norm);
CREATE INDEX idx_observations_run_predicate
  ON observations(run_id, predicate);
CREATE INDEX idx_observations_source
  ON observations(source, observed_at);
CREATE INDEX idx_claims_run_subject
  ON claims(run_id, subject_type, subject_value_norm);
CREATE INDEX idx_claims_run_object
  ON claims(run_id, object_type, object_value_norm);
CREATE INDEX idx_claim_evidence_observation
  ON claim_evidence(observation_id);
