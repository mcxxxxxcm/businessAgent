-- 智能客服核心数据表
-- 适用于 PostgreSQL 16 + pgvector 扩展
-- 执行方式: psql -U postgres -d cs_agent -f schema.sql

-- ============================================
-- 0. 启用pgvector扩展(如未启用)
-- ============================================
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================
-- 1. 商品表
-- ============================================
CREATE TABLE IF NOT EXISTS products (
    product_id    VARCHAR(20) PRIMARY KEY,
    name          VARCHAR(200) NOT NULL,
    category      VARCHAR(50) NOT NULL,
    price         DECIMAL(10, 2) NOT NULL,
    rating        DECIMAL(2, 1) DEFAULT 0.0,
    description   TEXT,
    brand         VARCHAR(100),
    created_at    TIMESTAMP DEFAULT NOW(),
    updated_at    TIMESTAMP DEFAULT NOW()
);

-- ============================================
-- 2. 库存表
-- ============================================
CREATE TABLE IF NOT EXISTS inventory (
    id            SERIAL PRIMARY KEY,
    product_id    VARCHAR(20) NOT NULL REFERENCES products(product_id),
    warehouse     VARCHAR(50) NOT NULL,
    available     INTEGER NOT NULL DEFAULT 0,
    restock_date  DATE,
    UNIQUE(product_id, warehouse)
);

-- ============================================
-- 3. 订单表
-- ============================================
CREATE TABLE IF NOT EXISTS orders (
    order_id      VARCHAR(30) PRIMARY KEY,
    user_id       VARCHAR(50) NOT NULL,
    status        VARCHAR(20) NOT NULL DEFAULT 'pending',
    total_amount  DECIMAL(10, 2) NOT NULL,
    created_at    TIMESTAMP DEFAULT NOW(),
    paid_at       TIMESTAMP,
    tracking_number VARCHAR(50),
    address       TEXT
);

-- 订单状态索引(高频查询: 按用户查订单)
CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);

-- ============================================
-- 4. 订单明细表
-- ============================================
CREATE TABLE IF NOT EXISTS order_items (
    id            SERIAL PRIMARY KEY,
    order_id      VARCHAR(30) NOT NULL REFERENCES orders(order_id),
    product_id    VARCHAR(20) NOT NULL REFERENCES products(product_id),
    product_name  VARCHAR(200) NOT NULL,
    quantity      INTEGER NOT NULL DEFAULT 1,
    price         DECIMAL(10, 2) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items(order_id);

-- ============================================
-- 5. 退款表
-- ============================================
CREATE TABLE IF NOT EXISTS refunds (
    refund_id     VARCHAR(30) PRIMARY KEY,
    order_id      VARCHAR(30) NOT NULL REFERENCES orders(order_id),
    refund_type   VARCHAR(20) NOT NULL DEFAULT 'return_refund',
    reason        TEXT,
    status        VARCHAR(20) NOT NULL DEFAULT 'pending_review',
    amount        DECIMAL(10, 2),
    created_at    TIMESTAMP DEFAULT NOW(),
    updated_at    TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_refunds_order_id ON refunds(order_id);

-- ============================================
-- 6. 售后工单表
-- ============================================
CREATE TABLE IF NOT EXISTS service_tickets (
    ticket_id     VARCHAR(30) PRIMARY KEY,
    order_id      VARCHAR(30) NOT NULL REFERENCES orders(order_id),
    issue_type    VARCHAR(20) NOT NULL,
    description   TEXT,
    priority      VARCHAR(10) NOT NULL DEFAULT 'normal',
    status        VARCHAR(20) NOT NULL DEFAULT 'open',
    created_at    TIMESTAMP DEFAULT NOW(),
    updated_at    TIMESTAMP DEFAULT NOW()
);

-- ============================================
-- 7. 知识库文章表(含向量列)
-- ============================================
CREATE TABLE IF NOT EXISTS knowledge_articles (
    id            SERIAL PRIMARY KEY,
    title         VARCHAR(200) NOT NULL,
    category      VARCHAR(50) NOT NULL,
    content       TEXT NOT NULL,
    embedding     vector(1024),  -- DashScope embedding维度(或按实际模型调整)
    created_at    TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_category ON knowledge_articles(category);
-- 向量索引(数据量大时启用，小数据量无需)
-- CREATE INDEX IF NOT EXISTS idx_knowledge_embedding ON knowledge_articles USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- ============================================
-- 8. 物流轨迹表
-- ============================================
CREATE TABLE IF NOT EXISTS logistics_tracks (
    id            SERIAL PRIMARY KEY,
    tracking_number VARCHAR(50) NOT NULL,
    carrier       VARCHAR(50),
    status        VARCHAR(20) NOT NULL DEFAULT 'pending',
    estimated_delivery DATE,
    created_at    TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS logistics_events (
    id            SERIAL PRIMARY KEY,
    tracking_number VARCHAR(50) NOT NULL,
    event_time    TIMESTAMP NOT NULL,
    location      VARCHAR(200),
    action        VARCHAR(200),
    FOREIGN KEY (tracking_number) REFERENCES logistics_tracks(tracking_number)
);

CREATE INDEX IF NOT EXISTS idx_logistics_events_tracking ON logistics_events(tracking_number);
