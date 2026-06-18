-- Migration 001: eager extraction cache fields
ALTER TABLE documents
  ADD COLUMN extraction_status      VARCHAR(50)  DEFAULT NULL,
  ADD COLUMN extraction_error       TEXT         DEFAULT NULL,
  ADD COLUMN extraction_prompt_hash VARCHAR(64)  DEFAULT NULL;
