-- Loaded only with the explicit demo profile. Credentials are synthetic fixtures.
INSERT INTO users(id,username,password_hash,role)
SELECT n,'buyer-'||n,'{noop}demo-buyer-'||n,'BUYER' FROM generate_series(1,1000) n;
INSERT INTO users VALUES (1001,'admin','{noop}demo-admin','ADMIN',now());
INSERT INTO categories SELECT n,'Category '||n FROM generate_series(1,20) n;
INSERT INTO products(id,sku,category_id,name,description,attributes)
SELECT n,'SKU-'||n,1+(n%20),'Product '||n,repeat('Synthetic product description. ',20),
       jsonb_build_object('brand','demo','model',n,'color','neutral')
FROM generate_series(1,100000) n;
INSERT INTO product_prices SELECT n,10000+n*100,'RUB' FROM generate_series(1,100000) n;
INSERT INTO inventory SELECT n,CASE WHEN n=1 THEN 100 WHEN n=2 THEN 0 ELSE 1000 END,0 FROM generate_series(1,100000) n;
INSERT INTO carts(user_id) SELECT id FROM users WHERE role='BUYER';
ANALYZE;
