ALTER TABLE media_uploads ADD COLUMN next_cleanup timestamptz NOT NULL DEFAULT now();
CREATE INDEX media_cleanup_due ON media_uploads(next_cleanup) WHERE state<>'READY';
