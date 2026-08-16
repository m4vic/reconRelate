ALTER TABLE model_calls ADD COLUMN output_disposition TEXT
  CHECK(output_disposition IS NULL OR output_disposition IN ('accepted', 'abstained', 'invalid'));
