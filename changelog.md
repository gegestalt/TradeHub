# Changelog

All notable changes to TradeHub will be documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Added
- `backend/` — full FastAPI backend skeleton on the `backend` branch
  - SQLAlchemy 2.0 async ORM models: `Competition`, `Player`, `Order`, `Position`, `PriceSnapshot`
  - Shared enums (`CompetitionState`, `OrderType`, `OrderSide`, etc.) in `models/enums.py`
  - `CompetitionService`: competition create/join/start lifecycle, unique lobby code generation, leaderboard scoring with VWAC position valuation
  - `OrderEngine`: market order fill, fee deduction, VWAC position tracking, short-sell guard, order cancellation
  - `DataAdapter` Protocol with `OfflineDataAdapter` (CSV/JSON from `/data`) and `OnlineDataAdapter` (yfinance)
  - REST routers: `POST/GET /competitions`, join, start, leaderboard; player portfolio; orders (place/list/cancel); prices
  - `GET /competitions/{code}/leaderboard/stream` — SSE live leaderboard
  - `GET /health` endpoint
  - Bearer token auth dependency; per-player order rate limit (10/minute)
  - Alembic migration scaffold with async-compatible `env.py`
  - `pyproject.toml` for `uv` package management; `ruff` configured (py311, line-length 100)
  - pytest suite: 14 tests covering competition lifecycle and order engine edge cases (in-memory SQLite)
- `data/.gitkeep` — placeholder for offline OHLCV data files
- `.env.example` — updated to reflect all backend config variables
- `.gitignore` — added Python, uv, SQLite, and data file patterns

---

## [0.1.0] — 2026-03-17

### Added
- `project_spec.md` — full product specification covering milestones (MVP → Not in Scope), architecture, data model, REST + SSE API surface, and data adapter interface
- `.env.example` — environment variable template for FastAPI backend, Next.js frontend, SQLite/PostgreSQL, and online data adapters (yfinance, CoinGecko, Alpha Vantage, exchangerate.host)
- `CLAUDE.md` — architecture overview, planned directory tree, design style guide, product/UX guidelines, security constraints, repo etiquette, common commands, and testing strategy
- `CHANGELOG.md` — this file
- `.gitignore` — covers Python, Node/Next.js, SQLite, and local data files
- GitHub repository initialized at `github.com/gegestalt/TradeHub`
