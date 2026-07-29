-- Wallet Trade Tracker — run this in the Supabase SQL editor after schema.sql

CREATE TABLE tracked_wallets (
  id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  address         TEXT        NOT NULL,
  chain           TEXT        NOT NULL DEFAULT 'SOL',
  label           TEXT,
  daily_report    BOOLEAN     NOT NULL DEFAULT TRUE,
  added_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_synced_at  TIMESTAMPTZ,
  UNIQUE(user_id, address)
);

CREATE TABLE trades (
  id            UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
  wallet_id     UUID          NOT NULL REFERENCES tracked_wallets(id) ON DELETE CASCADE,
  tx_hash       TEXT          NOT NULL UNIQUE,
  token_in      TEXT,
  token_out     TEXT,
  amount_in     NUMERIC(24,8),
  amount_out    NUMERIC(24,8),
  value_usd_in  NUMERIC(16,4),
  value_usd_out NUMERIC(16,4),
  pnl_usd       NUMERIC(16,4),
  is_win        BOOLEAN,
  traded_at     TIMESTAMPTZ   NOT NULL,
  synced_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_trades_wallet_date ON trades(wallet_id, traded_at DESC);
CREATE INDEX idx_trades_tx_hash     ON trades(tx_hash);
CREATE INDEX idx_wallets_user       ON tracked_wallets(user_id);
