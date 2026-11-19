-- Existing retired indexes receive a full grace period after this upgrade.
UPDATE search_indexes SET retired_at=now() WHERE state='RETIRED' AND retired_at IS NULL;
