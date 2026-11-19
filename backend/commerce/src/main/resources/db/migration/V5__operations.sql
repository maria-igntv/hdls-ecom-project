ALTER TABLE outbox_events ADD COLUMN trace_context jsonb NOT NULL DEFAULT '{}';
CREATE INDEX outbox_retention ON outbox_events(published_at,event_id) WHERE published_at IS NOT NULL;
CREATE INDEX inbox_retention ON consumer_inbox(processed_at,event_id);
CREATE INDEX webhook_retention ON payment_webhooks(processed_at,event_id);
CREATE INDEX reservation_items_product ON reservation_items(product_id,reservation_id);
CREATE INDEX payments_pending_age ON payments(created_at) WHERE state='PENDING';
CREATE INDEX payments_refund_age ON payments(updated_at) WHERE state='REFUND_PENDING';
ALTER TABLE search_indexes ADD COLUMN retired_at timestamptz;
