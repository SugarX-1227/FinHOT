CREATE TABLE IF NOT EXISTS finhot_market (kind text NOT NULL, key text NOT NULL, data jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (kind, key));
