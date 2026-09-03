# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A cryptocurrency price tracking and analysis tool (Chinese language UI). Supports BTC/ETH data from Binance, interactive candlestick charts, multiple prediction strategies, backtesting, paper trading portfolios, and LSTM model training.

## Common Commands

### Run the Web Application
```bash
python app.py
```
Runs Flask on port 5001. REST API plus single-page frontend served from `templates/index.html`.
Listens on 127.0.0.1 with debug off by default; override with `CRYPTO_HOST` / `CRYPTO_DEBUG=1`.

### Update Price Data
```bash
python main.py update          # Update all assets
python main.py update BTC      # Update only BTC
```
Fetches incremental data from Binance API and stores in SQLite.

### Train LSTM Model
```bash
python scripts/train_model.py --asset BTC --epochs 100 --lr 0.001 --patience 10
python scripts/train_model.py --asset BTC --epochs 100 --resume  # Resume from checkpoint
```
Supports checkpoint resume (`--resume`), early stopping (`--patience`), and per-asset checkpoint dirs.

### Run Backtest Comparison
```bash
python backtest_comparison.py
```
Compares all strategies and generates charts.

### Run Tests
```bash
python -m unittest tests/test_monte_carlo.py -v
python -m unittest tests/test_portfolio_fields.py -v
```

### CLI Chart / Predict
```bash
python main.py chart BTC --log --ma 20 60
python main.py predict --days 7
```

## Architecture

### Data Flow
```
Binance API → DataFetcher → SQLite (prices table) → Database.get_price_data()
                                          ↓
                              DataAggregator (day/week/month resample + MA)
                                          ↓
                              Frontend / Strategies / Backtest / ML
```

### Key Modules

**`src/data_fetcher.py`** — `DataFetcher` pulls from Binance API (`BASE_URL = https://api.binance.com/api/v3/klines`). Handles pagination (1000 bars per request). Maps asset codes via `TICKER_MAP` (e.g., BTC → BTCUSDT).

**`src/database.py`** — `Database` wraps SQLite. Main table is `prices` (OHLCV). Also manages `portfolios` and `portfolio_trades` tables for paper trading with FIFO realized PnL calculation.

**`src/aggregator.py`** — `DataAggregator` resamples daily data to weekly (`W-MON`) or monthly (`ME`) and computes moving averages. All downstream consumers go through this.

**`src/btc_predictor.py`** — Standalone `BTCPredictor` that combines technical indicators (RSI, MACD, Bollinger, ATR) with Monte Carlo simulation using geometric Brownian motion. Outputs `PredictionResult`.

### Strategy System (`src/strategies/`)

All strategies inherit from `PredictionStrategy` (ABC) in `base.py` and return a `StrategyResult` dataclass.

| Strategy | File | Description |
|----------|------|-------------|
| Monte Carlo | `monte_carlo.py` | Geometric Brownian motion with trend adjustment |
| Trend Following | `trend_following.py` | Multi-timeframe momentum |
| Mean Reversion | `mean_reversion.py` | RSI + Bollinger bands |
| Ensemble | `ensemble.py` | Weighted voting across multiple strategies |
| LSTM (ML) | `ml_strategy.py` | PyTorch LSTM with 20+ engineered features + attention |

**`ml_strategy.py`** is the most complex: `FeatureExtractor` produces 25+ features (returns, RSI variants, MACD, ADX, momentum, time features, volume). `LSTMPredictor` outputs `[price_return_pred, up_prob, volatility_pred]`. Models auto-load from `models/` by convention `{asset}_{model_id}_lstm_best.pth`.

### Backtest System (`src/backtest/`)

Two engines with different trade semantics:

**`engine.py` — `BacktestEngine`** — Discrete trades. At each `step_days` interval, runs `strategy.predict()`, records direction accuracy vs actual future price. Simulates trading via `metrics._simulate_trading()` with configurable `long_threshold`/`short_threshold`.

**`enhanced_engine.py` — `EnhancedBacktestEngine`** — Continuous positions with compound interest, stop-loss, take-profit, trailing stops, and dynamic position sizing. Tracks full `Trade` objects with entry/exit dates and PnL.

**`metrics.py`** — Computes `BacktestResult` metrics: direction accuracy, MAE/RMSE/MAPE, probability calibration, trading return/Sharpe/max drawdown, plus buy-and-hold baseline.

### Web Application (`app.py`)

Flask REST API (~1800 lines); the single-page frontend (Lightweight Charts + Chart.js) lives in `templates/index.html`. Key API groups:

- `/api/data/<asset>` — OHLC data with optional MA
- `/api/predict/<asset>` — Strategy predictions (Monte Carlo / Trend / LSTM)
- `/api/backtest/<asset>` — Single strategy backtest (`engine=standard|enhanced`)
- `/api/backtest/<asset>/compare` — Multi-strategy comparison
- `/api/train/*` — LSTM training job management (async with progress streaming)
- `/api/portfolios/*` — Paper trading CRUD + equity curve

Training jobs run in background threads with a `training_jobs` dict and `queue.Queue` for SSE streaming. Job progress/history is updated by the producer callback (works even with no SSE client attached); one running job per asset+model_id (409 on duplicates). Price reads go through a small `(asset, currency, latest_date)`-keyed cache that self-invalidates when new data lands.

### LSTM Training (`scripts/train_model.py`)

- Data split: 70% train / 30% validation by time (not random)
- Checkpoints saved every N epochs to `models/checkpoints/{asset}_{model_id}/`
- Best model saved by lowest validation loss: `models/{asset}_{model_id}_lstm_best.pth`
- Final model: `models/{asset}_{model_id}_lstm.pth`
- History JSON: `models/{asset}_{model_id}_lstm_history.json`

### Database Path Configuration

Default SQLite path is `~/data/sqlite/crypto_data.db`. Override via env var:
```bash
export CRYPTO_DB_PATH="/custom/path/crypto_data.db"
```
This exists to avoid WSL2 file locking issues when Windows IDEs access the Linux filesystem.

### Model Storage Conventions

Models in `models/` follow the pattern `{asset}_{model_id}_lstm_best.pth`. The `model_id` allows multiple models per asset (e.g., `btc_simple_lstm_best.pth`, `btc_optimized_lstm_best.pth`). `MLStrategyBase.find_best_model()` selects the one with lowest `best_val_loss`.

## Important Files

- `config.py` — Database path, supported assets, MA periods, chart style
- `requirements.txt` — Flask, pandas, yfinance, torch, scikit-learn, anthropic SDK
- `docs/LSTM_TRAINING.md` — Detailed training guide with checkpoint/resume examples
- `STRATEGIES.md` — Strategy usage examples and selection guide
- `BACKTEST_WEB_GUIDE.md` — Web API examples for enhanced backtesting
