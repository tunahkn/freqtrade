# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Freqtrade is a free, open-source crypto trading bot written in Python (>=3.10). It supports live/dry-run trading on major exchanges, backtesting, hyperopt parameter optimization, machine-learning-based adaptive modeling (FreqAI), and is controlled via Telegram, a REST API, or a web UI.

## Common Commands

The package installs a single `freqtrade` CLI entry point (`freqtrade.main:main`). Subcommands include `trade`, `backtesting`, `hyperopt`, `download-data`, `webserver`, `new-strategy`, `plot-dataframe`, `lookahead-analysis`, `recursive-analysis`, and more (see `freqtrade --help`).

### Testing
```bash
pytest                                              # full suite (uses pytest-xdist, loadscope dist)
pytest tests/test_<file>.py                         # single file
pytest tests/test_<file>.py::test_<method>          # single test
pytest --random-order --cov=freqtrade tests/        # what tests/pytest.sh runs (CI-style)
```
Tests use `pytest-asyncio` in `auto` mode and `pytest-mock`. Shared fixtures live in `tests/conftest.py` (and `conftest_trades*.py`, `conftest_hyperopt.py`). `tests/` mirrors the `freqtrade/` package layout.

### Lint, format, types (run before any PR)
```bash
ruff check .          # lint (line-length 100, rules configured in pyproject.toml)
ruff format .         # formatting
mypy freqtrade        # type checking (SQLAlchemy mypy plugin enabled)
pre-commit run -a     # runs ruff, mypy, codespell, and stubs together
```

### Setup
Native setup is driven by `./setup.sh` (Linux/macOS) or `setup.ps1` (Windows). Install editable for dev with the `dev` extra: `pip install -e ".[dev]"`. TA-Lib is a required C dependency.

## Repository Layout

- `freqtrade/` — main package.
- `ft_client/` — separate, independently-versioned `freqtrade-client` package (REST API client, `freqtrade_client`); it's a first-party dependency of the main package. Its tests are in `ft_client/test_client/`.
- `tests/` — test suite mirroring the package.
- `docs/` — mkdocs documentation (`mkdocs.yml`); published to freqtrade.io.
- `user_data/` — runtime user content (strategies, configs, data, models); excluded from packaging and most tooling.
- `config_examples/` — example configurations.

## Architecture

### Execution modes share a strategy
The same `IStrategy` subclass drives live trading, dry-run, and backtesting. Strategies are user code resolved at runtime; the bot calls strategy hooks but never the reverse. Key hooks in `freqtrade/strategy/interface.py`: `populate_indicators`, `populate_entry_trend`, `populate_exit_trend`, and optional callbacks (`custom_stoploss`, `custom_entry_price`, `custom_exit`, `custom_stake_amount`, `confirm_trade_entry`, etc.). Strategy parameters/hyperopt spaces are wired via `freqtrade/strategy/hyper.py` and `parameters.py`.

### Live/dry-run flow
`freqtrade/main.py` parses args (`commands/arguments.py`) and dispatches to a subcommand handler. `trade` runs `Worker` (`worker.py`), a state machine (`RUNNING`/`STOPPED`/`RELOAD_CONFIG`) that repeatedly calls `FreqtradeBot.process()` (`freqtradebot.py`). `FreqtradeBot` is the live orchestrator: it gathers the pair whitelist (pairlists), pulls data via the `DataProvider`, runs the strategy to produce signals, and places/manages orders through the exchange layer, persisting `Trade`/`Order` rows.

### Backtesting / hyperopt
`freqtrade/optimize/backtesting.py` re-implements the trade loop against historical OHLCV without a live exchange, reusing the same strategy and signal logic. `optimize/hyperopt/` wraps backtesting with scikit-optimize to search parameter spaces; loss functions live in `optimize/hyperopt_loss/`. Reports are generated under `optimize/optimize_reports/`.

### Resolvers (dynamic loading)
`freqtrade/resolvers/` loads user-supplied and built-in pluggable components by name from config: strategies, exchanges, pairlist handlers, protections, hyperopt loss functions, and FreqAI models. `IResolver` (`iresolver.py`) is the base; this is how user_data strategies/models get discovered.

### Exchange layer
`freqtrade/exchange/` wraps **ccxt**. `exchange.py` holds the generic `Exchange` class; per-exchange subclasses (`binance.py`, `bybit.py`, `okx.py`, `kraken.py`, `hyperliquid.py`, …) override quirks. Handles spot and (experimental) futures/leverage. `exchange_ws.py` is the websocket data path.

### Persistence
`freqtrade/persistence/` uses **SQLAlchemy 2.0**. Core models `Trade`/`Order` (`trade_model.py`), plus `pairlock.py`, `custom_data.py`, `key_value_store.py`. Schema changes go through `migrations.py` (manual, in-code migrations — add to it rather than relying on autogeneration). Default backend is SQLite.

### RPC / API
`freqtrade/rpc/` exposes the bot. `rpc.py` is the transport-agnostic core; `telegram.py`, `webhook.py`, `discord.py`, and `api_server/` (FastAPI + uvicorn, serves the REST API and bundled FreqUI) are front-ends. The `ft_client` package is the matching Python REST client. `external_message_consumer.py` lets bots consume signals from other bots.

### Pairlists & Protections
`freqtrade/plugins/pairlist/` — chainable pairlist handlers (StaticPairList, VolumePairList, filters) managed by `pairlistmanager.py`, producing the tradable whitelist. `freqtrade/plugins/protections/` — global/per-pair stop conditions managed by `protectionmanager.py`.

### FreqAI
`freqtrade/freqai/` is the adaptive-ML subsystem (optional `freqai`/`freqai_rl` extras). `freqai_interface.py` is the base model interface; `data_kitchen.py`/`data_drawer.py` handle feature engineering and model storage; `prediction_models/` and `RL/` hold concrete models. Heavy deps (torch, lightgbm, xgboost, catboost) are optional.

## Conventions

- Line length **100** everywhere (ruff, isort, black config). `known_first_party = ["freqtrade_client"]`, `lines_after_imports = 2`.
- Public methods need docstrings in double-quoted reST format (`:param x:`, `:return:`, `:raises X:`).
- Type hints are expected; `mypy freqtrade` must pass (tests are excluded from mypy).
- **Always branch from and PR against `develop`, never `stable`.** New features require unit tests and docs in the same PR.
- Some ruff bandit (`S...`) and pathlib (`PTH`) rules apply; tests and FreqAI have per-directory ignores configured in `pyproject.toml`.
