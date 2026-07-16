# A-Share Stock Selection System Development Guide

## 1. Product Goal

This project is a personal A-share research and stock selection system. It is not designed to predict certain winners. It is designed to answer one practical question every trading day:

> Based on historical validation, which stocks currently have stronger statistical odds of positive or excess returns over the next 5, 20, or 60 trading days?

The first version focuses on a transparent MVP:

- MySQL as the primary structured data store.
- Tushare Pro as the primary structured A-share data source.
- Tushare replaces the previous AKShare integration.
- Daily bars, valuation, liquidity, industry, factor values, model predictions, and backtest summaries.
- A light-theme web dashboard for screening, ranking, factor explanation, and backtest review.

This system is for research only and does not provide investment advice.

## 2. MVP Scope

### Included

- A-share stock master data.
- Daily OHLCV data.
- Forward-adjusted daily bars for factor calculation.
- Daily basic fields such as turnover, PE, PB, market cap when available.
- Price-volume factors with daily cross-sectional normalization.
- Rolling logistic models and out-of-sample probabilities for 5/20/60-day horizons.
- Capital-aware non-overlapping walk-forward backtests with whole-lot sizing and explicit proportional costs.
- API endpoints for recommendations, stock explanation, backtest summary, and system status.
- Static white/light frontend served by the backend.
- MySQL schema and data-source adapters.
- Demo mode so the UI and APIs can run before MySQL is configured.

### Deferred

- LightGBM as a nonlinear challenger model.
- Full Qlib integration.
- Minute/tick data.
- Real-time trading or brokerage integration.
- Portfolio rebalancing execution.
- User login and multi-account permissions.

## 3. Recommended Data Sources

### Primary source

- Tushare Pro: stock master, historical daily bars, trading calendar, adjustment factors, and daily valuation/liquidity fields.
- Full-market history is requested by trade date, matching Tushare's official recommendation.
- API responses are paged at 5000 rows, below Tushare's 6000-row endpoint cap.
- `daily` volume is converted from lots to shares and amount from thousands of yuan to yuan.
- Research OHLC uses the stable backward-adjusted formula `raw price * adj_factor`.
- Every open date validates unique keys, exact returned date, OHLCV invariants, adjustment-factor coverage, and at least 98% daily-basic coverage before writing.
- `data_sync_state` keeps separate checkpoints for full-market and code-filtered scopes. A checkpoint advances only after raw bars, factors, daily indicators, and adjusted bars all commit successfully.

### Optional sources

- JQData: professional data source with richer factor, minute, tick, and risk-model support.
- CNINFO and exchange sites: announcements and disclosures for later point-in-time fundamental data.

The data access layer must hide provider differences behind a small adapter interface so the rest of the system does not depend on one provider.

## 4. Architecture

```text
frontend/static
  -> light dashboard: recommendations, filters, factor explanation, backtest cards

backend API
  -> FastAPI routes
  -> recommendation service
  -> factor scoring service
  -> repository layer

data layer
  -> MySQL tables
  -> Tushare Pro adapter
  -> optional JQData adapter later

research layer
  -> factor calculation
  -> label generation
  -> backtest and IC validation
  -> model prediction storage
```

### Application Boundaries

- Analysis workspace: recommendation filters, backtest KPIs, ranked stocks, and factor explanation.
- Data management workspace: provider credential state, sync parameters, task progress, logs, checkpoints, and table inventory.
- FastAPI request layer: validates commands and returns state; it does not perform the long-running download inside the request.
- In-process job manager: owns one cooperative background thread, one stop event, and a bounded 200-entry log buffer.
- Tushare sync service: shared by the CLI and web job manager so pagination, validation, persistence, and checkpoint behavior cannot diverge.
- Repository layer: owns MySQL upserts, metadata inventory, and durable sync checkpoints.

The first local version intentionally permits only one active data job. Run Uvicorn with one worker. A future multi-user deployment should replace the in-process manager with a durable queue such as Celery/RQ and persist job events.

## 5. Backend Structure

```text
backend/app/
  main.py                 FastAPI app and routes
  config.py               environment-driven settings
  db.py                   SQLAlchemy engine/session setup
  models.py               SQLAlchemy table models
  repositories.py         MySQL repository and demo fallback
  services/
    data_sources.py       Tushare data adapter and field conversion
    demo_data.py          deterministic demo data
    scoring.py            factor normalization, score, probability
  research/
    factors.py            trailing factors and cross-sectional normalization
    modeling.py           labels and purged walk-forward training
    backtest.py           capital-aware whole-lot portfolio evaluation
    storage.py            bulk MySQL research I/O
  sql/
    schema.sql            MySQL DDL
  static/
    index.html            dashboard
    styles.css            light UI styling
    app.js                API calls and rendering
```

## 6. MySQL Design

### Core Tables

- `dim_stock`: stock identity, name, exchange, listing date, status, industry.
- `trade_calendar`: exchange trading dates.
- `daily_bar`: unadjusted daily OHLCV.
- `adj_factor`: raw Tushare adjustment factors.
- `daily_bar_adj`: stable backward-adjusted daily OHLCV for factor calculation.
- `daily_basic`: valuation, turnover, market cap, ST flag.
- `data_sync_state`: provider/dataset/scope checkpoint and last failure.
- `factor_daily`: long-format factor table.
- `model_prediction`: stock-level score and horizon probabilities.
- `backtest_summary`: saved validation metrics.

### Important Indexes

- `(code, trade_date)` for stock time series reads.
- `(trade_date, code)` for cross-sectional reads.
- `(trade_date, factor_name)` for factor ranking.
- `(trade_date, horizon, score)` for recommendations.

For the MVP, factors are stored in long format because it is simple to add new factors without changing schema. If factor count grows heavily, a wide materialized table can be added later.

## 7. Stock Selection Method

The production research path uses the following factors:

### Factor Set

Each stock receives factor values:

- `momentum_20d`, `momentum_60d`: trailing adjusted-price returns.
- `reversal_5d`: negative trailing five-day return.
- `trend_20d`: close relative to its 20-day moving average.
- `low_volatility_20d`: negative annualized realized volatility.
- `drawdown_60d`: close relative to its 60-day rolling high.
- `liquidity_20d`: log mean daily amount.
- `turnover_20d`: mean turnover rate.

Each date is processed independently: 1%/99% winsorization followed by a cross-sectional z-score. A stock must have a continuous 60-market-session history, complete factors, and meet the minimum amount filter.

### Horizon Probability and Timing

The label and execution convention is:

```text
signal time = close(t)
entry       = open(t + 1)
exit        = open(t + H + 1)
label_H     = exit / entry - 1 > 0
```

A regularized logistic regression is refit on every rebalance date. The training window defaults to 756 market sessions. The final training label must be fully observed by the prediction close, so an `H+1` market-session purge is applied. The displayed probability is a genuinely out-of-sample model estimate for historical rebalance dates; it is an estimate, not a guarantee.

The baseline deliberately avoids balanced class weights because those weights alter the observed positive-return prior and make the raw probability harder to interpret. Nonlinear models should be treated as challenger models and compared on calibration, log loss, Rank IC, and portfolio return.

## 8. API Design

### `GET /api/health`

Returns service mode, database status, and whether demo mode is enabled.

### `GET /api/recommendations?horizon=20d&limit=20`

Returns ranked stock recommendations:

- code
- name
- industry
- score
- probability
- factor highlights
- risk flags
- last close
- trade date

### `GET /api/stocks/{code}/explain?horizon=20d`

Returns a single-stock explanation:

- latest prediction
- factor contributions
- risk notes
- comparable ranks

### `GET /api/backtest/summary?horizon=20d`

Returns stored validation metrics:

- top group return
- benchmark return
- win rate
- max drawdown
- Sharpe
- rank IC
- turnover

Data synchronization is implemented as a shared service. The CLI runs it directly; the web API starts it in a background thread and returns immediately rather than holding a long-running HTTP request open.

For each trade date, `daily`, `adj_factor`, and `daily_basic` are requested concurrently with a 6,000-row page size. Validation remains synchronous, and all four persisted datasets plus the checkpoint still commit in one ordered transaction. The 5,000-point profile does not use a fixed inter-date delay; failed requests retain exponential retry backoff.

Daily persistence uses a safely parameterized PyMySQL multi-row statement (up to 6,000 rows) instead of constructing thousands of SQLAlchemy bind nodes. Each core table retains only the primary key, the idempotency key `(code, trade_date)`, and a narrow `trade_date` index; redundant `code` and `(trade_date, code)` indexes are removed by the one-time initializer. On the local 32 GB workstation, InnoDB's buffer pool is persistently configured at 2 GB. Binlog, doublewrite, and flush-at-commit durability remain enabled.

`scripts/check_tushare.py` performs a read-only token, permission, pagination, and one-date quality check. `sync_tushare.py` stops at the first failed date by default so an incremental checkpoint cannot skip a historical gap.

### Data management API

- `GET /api/data/config`: provider, full-history start, and checkpoint policy. Token state and token contents are not returned.
- `GET /api/data/inventory`: estimated table rows, date bounds, and durable checkpoints.
- `POST /api/data/sync`: starts one background task and returns HTTP 202.
- `POST /api/data/sync/stop`: requests cooperative cancellation after the active provider/database operation.
- `GET /api/data/sync/status`: progress percentage, current date, row totals, failures, timestamps, and bounded logs.

The web UI does not accept a date range or a Token. A new Shanghai/Shenzhen history job starts at `2018-01-01` and runs through the current date; Beijing-market instruments are excluded at ingestion and research-query boundaries. The Token is read only from the project `.env`. All tables for one market date and its checkpoint commit in one database transaction. Completion, failure, cooperative stop, or process termination can therefore resume from the next safe calendar date.

## 9. Frontend Design

The UI uses a white/light theme:

- White page background.
- Neutral gray borders.
- Restrained blue/green accents for actions and positive metrics.
- Dense dashboard layout, not a marketing landing page.
- Recommendation table as the primary view.
- Compact KPI cards for backtest summary.
- Filter controls for horizon, limit, and minimum score.
- Side panel style stock explanation section.
- Top-level tabs separate analysis from operational data management.
- Data management uses a compact form, stable progress bar, four row counters, inventory table, and scrolling task log.
- Polling reads only task state every 1.2 seconds; table inventory refreshes manually or when a task reaches a terminal state.

Frontend implementation is static HTML/CSS/JavaScript to keep the MVP simple and avoid a build step. If the app grows, it can be migrated to React while keeping the same API.

## 10. Development Phases

### Phase 0: Skeleton

- Create docs, backend app, schema, demo data, and frontend shell.

### Phase 1: Data Persistence

- Configure MySQL.
- Create schema.
- Sync stock list, calendar, raw bars, adjustment factors, and daily indicators from Tushare.
- Store daily bars and basic valuation data.

### Phase 2: Factor Engine (implemented)

- Calculate core price-volume factors.
- Store factors in `factor_daily`.
- Generate model features and rolling probabilities.

### Phase 3: Backtest (implemented)

- Generate forward labels.
- Validate capital-aware ranked portfolios and top quantiles.
- Store backtest summary.

### Phase 4: Model Upgrade (baseline implemented)

- The sklearn logistic baseline trains on purged rolling windows.
- Add probability calibration reports and a LightGBM challenger.
- Add a dedicated model-run metadata table and serialized artifacts.

### Phase 5: Data Source Upgrade

- Add JQData as a secondary provider and compare data quality.
- Add data quality checks and provider comparison reports.

## 11. Operational Notes

- Use MySQL `utf8mb4` charset.
- Store all trade dates as `DATE`.
- Keep raw provider data immutable when possible.
- Never calculate labels using future data in the feature window.
- Apply point-in-time ST, suspension, listing-age, and liquidity filters when those fields are available.
- Treat limit-up and limit-down days carefully in backtests.
- Add transaction cost and slippage before trusting any strategy result.

### Current research limits

- Tushare stock lists include current, paused, pending, and delisted statuses, reducing survivorship bias. Exact historical universe membership still requires listing/delisting-date filters during each cross section.
- The current Tushare path does not yet populate point-in-time ST intervals. Suspensions are handled conservatively through missing exact trading-day prices, but historical ST filtering needs a dated status source.
- Daily bars cannot prove whether an order at a sealed limit-up/limit-down price would have filled. Accurate fill simulation requires limit-state or finer-grained data.
- For accounts between CNY 10,000 and CNY 100,000, recommendations scale from three to ten positions, use 100-share lots, skip unaffordable first lots, and retain a 3% cash buffer.
- The capital-aware backtest applies proportional commission without a minimum fee, sell-side stamp duty, and slippage.
- The stored maximum drawdown uses rebalance-period endpoints. Intraperiod daily mark-to-market drawdown is a later extension.
- Price-volume factors are implemented now. Point-in-time fundamentals must not be added until announcement dates and revision history are available.

## 12. Local Run Strategy

The project supports two modes:

```text
APP_DEMO_MODE=true
```

Runs without MySQL and returns deterministic demo recommendations.

```text
APP_DEMO_MODE=false
DATABASE_URL=mysql+pymysql://root:your_password@localhost:3306/stock_selector?charset=utf8mb4
```

Uses MySQL and real persisted data.

The local `.env` selects MySQL mode and stores `TUSHARE_TOKEN`. Run `python scripts/init_mysql.py` once, pull bars with `scripts/sync_tushare.py`, then run `scripts/run_research.py`.
