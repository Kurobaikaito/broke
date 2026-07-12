CREATE DATABASE IF NOT EXISTS stock_selector
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE stock_selector;

CREATE TABLE IF NOT EXISTS dim_stock (
  code VARCHAR(16) PRIMARY KEY,
  name VARCHAR(64) NOT NULL,
  exchange VARCHAR(16),
  industry VARCHAR(64),
  list_date DATE,
  status VARCHAR(16) NOT NULL DEFAULT 'active',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS trade_calendar (
  trade_date DATE NOT NULL,
  exchange VARCHAR(16) NOT NULL DEFAULT 'SSE',
  is_open TINYINT NOT NULL DEFAULT 1,
  PRIMARY KEY (trade_date, exchange)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS daily_bar (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  code VARCHAR(16) NOT NULL,
  trade_date DATE NOT NULL,
  open DECIMAL(18,4),
  high DECIMAL(18,4),
  low DECIMAL(18,4),
  close DECIMAL(18,4),
  volume DECIMAL(24,4),
  amount DECIMAL(24,4),
  pct_chg DECIMAL(12,4),
  turnover_rate DECIMAL(12,4),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_daily_bar_code_date (code, trade_date),
  KEY ix_daily_bar_trade_date_code (trade_date, code),
  KEY ix_daily_bar_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS daily_basic (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  code VARCHAR(16) NOT NULL,
  trade_date DATE NOT NULL,
  pe_ttm DECIMAL(18,4),
  pb DECIMAL(18,4),
  ps_ttm DECIMAL(18,4),
  total_mv DECIMAL(24,4),
  float_mv DECIMAL(24,4),
  turnover_rate DECIMAL(12,4),
  is_st TINYINT NOT NULL DEFAULT 0,
  is_suspended TINYINT NOT NULL DEFAULT 0,
  limit_status TINYINT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_daily_basic_code_date (code, trade_date),
  KEY ix_daily_basic_trade_date_code (trade_date, code),
  KEY ix_daily_basic_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS data_sync_state (
  provider VARCHAR(32) NOT NULL,
  dataset VARCHAR(64) NOT NULL,
  scope VARCHAR(64) NOT NULL,
  last_trade_date DATE,
  last_row_count INT,
  status VARCHAR(16) NOT NULL DEFAULT 'ready',
  error_message TEXT,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (provider, dataset, scope)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS adj_factor (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  code VARCHAR(16) NOT NULL,
  trade_date DATE NOT NULL,
  adj_factor DECIMAL(24,8) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_adj_factor_code_date (code, trade_date),
  KEY ix_adj_factor_trade_date_code (trade_date, code),
  KEY ix_adj_factor_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS daily_bar_adj (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  code VARCHAR(16) NOT NULL,
  trade_date DATE NOT NULL,
  open DECIMAL(18,4),
  high DECIMAL(18,4),
  low DECIMAL(18,4),
  close DECIMAL(18,4),
  volume DECIMAL(24,4),
  amount DECIMAL(24,4),
  pct_chg DECIMAL(12,4),
  turnover_rate DECIMAL(12,4),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_daily_bar_adj_code_date (code, trade_date),
  KEY ix_daily_bar_adj_trade_date_code (trade_date, code),
  KEY ix_daily_bar_adj_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS factor_daily (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  code VARCHAR(16) NOT NULL,
  trade_date DATE NOT NULL,
  factor_name VARCHAR(64) NOT NULL,
  factor_value DECIMAL(24,8),
  factor_zscore DECIMAL(18,8),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_factor_daily (code, trade_date, factor_name),
  KEY ix_factor_daily_date_name (trade_date, factor_name),
  KEY ix_factor_daily_code_date (code, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS model_prediction (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  code VARCHAR(16) NOT NULL,
  trade_date DATE NOT NULL,
  horizon VARCHAR(16) NOT NULL,
  model_version VARCHAR(64) NOT NULL DEFAULT 'rule-v1',
  score DECIMAL(18,6) NOT NULL,
  probability DECIMAL(10,6) NOT NULL,
  rank_no INT,
  factor_snapshot TEXT,
  risk_flags TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_model_prediction (code, trade_date, horizon, model_version),
  KEY ix_model_prediction_rank (trade_date, horizon, score),
  KEY ix_model_prediction_code_date (code, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS backtest_summary (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  horizon VARCHAR(16) NOT NULL,
  model_version VARCHAR(64) NOT NULL DEFAULT 'rule-v1',
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,
  top_group_return DECIMAL(18,6),
  benchmark_return DECIMAL(18,6),
  win_rate DECIMAL(10,6),
  max_drawdown DECIMAL(10,6),
  sharpe DECIMAL(10,6),
  rank_ic DECIMAL(10,6),
  turnover DECIMAL(10,6),
  notes TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_backtest_summary (horizon, model_version, start_date, end_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
