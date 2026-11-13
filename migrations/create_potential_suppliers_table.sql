-- Universal Runner Marketplace
-- Phase 2: create the separate table for unverified supplier leads.
-- Safe, additive migration: does not alter or delete existing tables or records.

CREATE TABLE IF NOT EXISTS potential_supplier (
    id SERIAL PRIMARY KEY,
    supplier_name VARCHAR(150) NOT NULL,
    country VARCHAR(100) NOT NULL,
    website_url VARCHAR(500),
    category VARCHAR(100),
    contact_details VARCHAR(200),
    estimated_price DOUBLE PRECISION,
    currency VARCHAR(3),
    source VARCHAR(255),
    notes TEXT,
    verification_status VARCHAR(20) NOT NULL DEFAULT 'Pending',
    sourcing_request_id INTEGER REFERENCES sourcing_request(id),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_potential_supplier_verification_status
    ON potential_supplier (verification_status);

CREATE INDEX IF NOT EXISTS ix_potential_supplier_sourcing_request_id
    ON potential_supplier (sourcing_request_id);
