ALTER TABLE plan_entitlements
    ADD COLUMN IF NOT EXISTS paddle_price_id TEXT;

ALTER TABLE billing_subscriptions
    ADD COLUMN IF NOT EXISTS provider_transaction_id TEXT;
