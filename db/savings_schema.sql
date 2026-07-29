-- Savings Tracker — run this in the Supabase SQL editor after wallet_schema.sql

CREATE TABLE savings_goals (
  id           UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  daily_target NUMERIC(12,2) NOT NULL,
  started_at   DATE          NOT NULL DEFAULT CURRENT_DATE,
  updated_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
  UNIQUE(user_id)
);

CREATE TABLE savings_logs (
  id         UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  amount     NUMERIC(12,2) NOT NULL,
  log_date   DATE          NOT NULL DEFAULT CURRENT_DATE,
  created_at TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_savings_logs_user_date ON savings_logs(user_id, log_date DESC);
CREATE INDEX idx_savings_goals_user     ON savings_goals(user_id);
