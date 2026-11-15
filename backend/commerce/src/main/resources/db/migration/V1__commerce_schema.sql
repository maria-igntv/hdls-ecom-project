CREATE TABLE users (
    id bigint PRIMARY KEY, username text NOT NULL UNIQUE,
    password_hash text NOT NULL, role text NOT NULL CHECK (role IN ('BUYER','ADMIN')),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE categories (id bigint PRIMARY KEY, name text NOT NULL);
CREATE TABLE products (
    id bigint PRIMARY KEY, sku text NOT NULL UNIQUE,
    category_id bigint NOT NULL REFERENCES categories,
    name text NOT NULL, description text NOT NULL DEFAULT '', attributes jsonb NOT NULL DEFAULT '{}',
    active boolean NOT NULL DEFAULT true, version bigint NOT NULL DEFAULT 1 CHECK (version > 0)
);
CREATE INDEX products_category_cursor ON products(category_id,id) WHERE active;
CREATE TABLE product_prices (
    product_id bigint PRIMARY KEY REFERENCES products,
    price_minor bigint NOT NULL CHECK (price_minor BETWEEN 1 AND 100000000000),
    currency text NOT NULL DEFAULT 'RUB' CHECK (currency = 'RUB')
);
CREATE TABLE inventory (
    product_id bigint PRIMARY KEY REFERENCES products,
    on_hand integer NOT NULL CHECK (on_hand >= 0),
    reserved integer NOT NULL DEFAULT 0 CHECK (reserved >= 0 AND reserved <= on_hand)
);
CREATE TABLE product_images (
    product_id bigint NOT NULL REFERENCES products, object_key text NOT NULL,
    content_type text NOT NULL, size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
    PRIMARY KEY(product_id,object_key)
);
CREATE TABLE carts (
    user_id bigint PRIMARY KEY REFERENCES users, version bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE cart_items (
    user_id bigint NOT NULL REFERENCES carts ON DELETE CASCADE,
    product_id bigint NOT NULL REFERENCES products,
    quantity integer NOT NULL CHECK (quantity BETWEEN 1 AND 100), PRIMARY KEY(user_id,product_id)
);
CREATE TABLE orders (
    id uuid PRIMARY KEY, user_id bigint NOT NULL REFERENCES users,
    status text NOT NULL CHECK (status IN ('PENDING_PAYMENT','PAID','EXPIRED')),
    total_minor bigint NOT NULL CHECK (total_minor >= 0), currency text NOT NULL DEFAULT 'RUB' CHECK (currency='RUB'),
    idempotency_key varchar(80) NOT NULL, request_hash char(64) NOT NULL,
    checkout_response jsonb NOT NULL DEFAULT '{}', version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
    UNIQUE(user_id,idempotency_key)
);
CREATE INDEX orders_user_cursor ON orders(user_id,created_at DESC,id DESC);
CREATE INDEX orders_expiry ON orders(expires_at,id) WHERE status='PENDING_PAYMENT';
CREATE TABLE order_items (
    order_id uuid NOT NULL REFERENCES orders ON DELETE CASCADE,
    product_id bigint NOT NULL REFERENCES products, name_snapshot text NOT NULL,
    price_minor bigint NOT NULL CHECK (price_minor > 0), quantity integer NOT NULL CHECK (quantity BETWEEN 1 AND 100),
    PRIMARY KEY(order_id,product_id)
);
CREATE TABLE reservations (
    id uuid PRIMARY KEY, order_id uuid NOT NULL UNIQUE REFERENCES orders ON DELETE CASCADE,
    state text NOT NULL CHECK (state IN ('ACTIVE','CONFIRMED','RELEASED')), expires_at timestamptz NOT NULL
);
CREATE TABLE reservation_items (
    reservation_id uuid NOT NULL REFERENCES reservations ON DELETE CASCADE,
    product_id bigint NOT NULL REFERENCES products, quantity integer NOT NULL CHECK (quantity > 0),
    PRIMARY KEY(reservation_id,product_id)
);
CREATE TABLE payments (
    id uuid PRIMARY KEY, order_id uuid NOT NULL REFERENCES orders,
    provider_key text NOT NULL UNIQUE, amount_minor bigint NOT NULL CHECK (amount_minor > 0),
    state text NOT NULL CHECK (state IN ('PENDING','SUCCEEDED','FAILED','REFUND_PENDING','REFUNDED')),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX payments_order ON payments(order_id);
CREATE TABLE idempotency_keys (
    user_id bigint NOT NULL REFERENCES users, key varchar(80) NOT NULL, request_hash char(64) NOT NULL,
    response_status integer, response_body jsonb,
    created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(user_id,key),
    CHECK ((response_status IS NULL) = (response_body IS NULL))
);
CREATE INDEX idempotency_retention ON idempotency_keys(created_at);
CREATE TABLE outbox_events (
    event_id uuid PRIMARY KEY, aggregate_type text NOT NULL, aggregate_id text NOT NULL,
    aggregate_version bigint NOT NULL, event_type text NOT NULL, payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz,
    UNIQUE(aggregate_type,aggregate_id,aggregate_version,event_type)
);
CREATE INDEX outbox_unpublished ON outbox_events(created_at,event_id) WHERE published_at IS NULL;
CREATE TABLE consumer_inbox (
    consumer text NOT NULL, event_id uuid NOT NULL,
    processed_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(consumer,event_id)
);
