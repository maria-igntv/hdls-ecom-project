-- Representative history for query plans, separate from the checkout experiment.
INSERT INTO orders(id,user_id,status,total_minor,idempotency_key,request_hash,expires_at,created_at)
SELECT md5('history-'||n)::uuid,1+(n%1000),'EXPIRED',30000,'history-'||n,repeat(md5(n::text),2),
       now()-interval '31 days'-n*interval '1 second',now()-interval '32 days'-n*interval '1 second'
FROM generate_series(1,200000) n;
INSERT INTO order_items(order_id,product_id,name_snapshot,price_minor,quantity)
SELECT md5('history-'||n)::uuid,p,'Archived product '||p,10000,1
FROM generate_series(1,200000) n CROSS JOIN generate_series(1,3) p;
-- Reservations older than the modelled 30-day retention need not be retained.
INSERT INTO outbox_events(event_id,aggregate_type,aggregate_id,aggregate_version,event_type,payload,published_at)
SELECT md5('event-'||n)::uuid,'Fixture',n::text,1,'Archived','{}',now()
FROM generate_series(1,100000) n;
ANALYZE;
