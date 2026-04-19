# Project Status

> Last updated: 2026-04-19
> Current phase: **Backend v0.4 — spec-aligned, frontend not started**

---

## Where We Left Off

All backend work lives on the `backend` branch. `main` is untouched at project init.
116 tests pass. Ruff-clean. Frontend has not started.

---

## Current State

| Area | Status | Notes |
|---|---|---|
| Product spec | Done | `project_spec.md` — milestones, data model, API surface |
| Repo & git | Done | `github.com/gegestalt/TradeHub` — all work on `backend` branch |
| Env config | Done | `.env.example`, `config.py` covers all settings |
| CLAUDE.md | Done | Architecture, conventions, commands, test strategy |
| Backend skeleton | Done | FastAPI app, models, DB, routers, lifespan |
| Competition lifecycle | Done | `lobby → active → ended`; creator starts/ends; auto-end on `end_at` |
| Order engine | Done | Market, limit, stop-loss, take-profit, stop-limit, OCO; FIFO matching |
| Time-in-force | Done | GTC, IOC, FOK |
| Data adapters | Done | OfflineAdapter (CSV/JSON), OnlineAdapter (yfinance), MockAdapter |
| Competition API | Done | Create, join, start, **end**, leaderboard, SSE stream |
| Player API | Done | Portfolio, **P&L history**, order management |
| Lobby API | Done | `POST /lobbies` → UUID + code; `GET /lobbies/{id}` |
| Spectator mode | Done | `JoinRequest.spectator=True`; can join active comps; excluded from leaderboard/orders |
| max_players cap | Done | `CompetitionCreate.max_players` enforced on join |
| Price snapshots | Done | Background task writes `PriceSnapshot` every 60s for active competitions |
| Portfolio snapshots | Done | Background task writes `PortfolioSnapshot` every 60s per player |
| P&L history | Done | `GET /players/{id}/history` returns chronological portfolio value snapshots |
| Sharpe ratio scoring | Done | Computed from `PortfolioSnapshot` series when `scoring_method=sharpe_ratio` |
| Technical indicators | Done | RSI, MACD, EMA, SMA, Bollinger Bands, ATR, Stochastic, VWAP, OBV |
| Multi-timeframe OHLCV | Done | `GET /prices/{ticker}/candles?timeframe=1m\|5m\|…\|1w` |
| Order book depth | Done | `GET /competitions/{code}/orderbook/{ticker}` |
| Screener | Done | `GET /competitions/{code}/screener` |
| Price alerts | Done | CRUD at `/players/{id}/alerts` |
| Watchlist | Done | CRUD at `/players/{id}/watchlist` |
| WebSocket streaming | Done | `WS /ws/{code}` — prices + leaderboard every 3s |
| Price cache | Done | 5s TTL in-memory cache (Redis-swappable) |
| Audit logging | Done | Structured JSON to `tradehub.audit` logger |
| Metrics | Done | In-process metrics at `GET /metrics` |
| CI/CD | Done | `.github/workflows/ci.yml` — ruff + pytest on push/PR |
| Tests (backend) | Done | **116 pytest tests**: unit, property-based (Hypothesis), concurrency, lifecycle |
| Frontend skeleton | Not started | Next.js app, layout, basic pages |
| UI: Dashboard | Not started | Charts, positions table, trade form |
| Tests (frontend) | Not started | Vitest |

---

## Milestone Progress

```
[MVP  ]  █████████░  ~85%  Backend complete; frontend not started
[V1   ]  ████░░░░░░  ~40%  Order types ✓, shorts ✓, Sharpe ✓, spectator ✓; frontend missing
[V2   ]  ██░░░░░░░░  ~15%  Portfolio analytics groundwork in place
[Later]  ░░░░░░░░░░   0%   Scheduled comps, commentary, mobile polish
```

---

## API Surface (current)

### Lobbies
| Method | Path | Description |
|---|---|---|
| POST | `/lobbies` | Create lobby, returns UUID + lobby_code + token |
| GET | `/lobbies/{id}` | Get lobby attributes by UUID |

### Competitions
| Method | Path | Description |
|---|---|---|
| POST | `/competitions` | Create competition (full config) |
| GET | `/competitions/{code}` | Get by lobby code |
| POST | `/competitions/{code}/join` | Join as player or spectator |
| POST | `/competitions/{code}/start` | Creator starts competition |
| POST | `/competitions/{code}/end` | Creator ends competition |
| GET | `/competitions/{code}/leaderboard` | Current rankings (total_value or Sharpe) |
| GET | `/competitions/{code}/leaderboard/stream` | SSE live leaderboard |
| GET | `/competitions/{code}/trades` | Recent fill feed |

### Players & Orders
| Method | Path | Description |
|---|---|---|
| GET | `/players/{id}/portfolio` | Portfolio state with unrealized P&L |
| GET | `/players/{id}/history` | P&L over time (portfolio snapshots) |
| POST | `/players/{id}/orders` | Place order (market/limit/stop/take-profit/OCO) |
| GET | `/players/{id}/orders` | List orders (filterable by status) |
| DELETE | `/players/{id}/orders/{id}` | Cancel pending order |
| GET | `/players/{id}/alerts` | List price alerts |
| POST | `/players/{id}/alerts` | Create price alert |
| DELETE | `/players/{id}/alerts/{id}` | Delete alert |
| GET | `/players/{id}/watchlist` | Watchlist tickers |
| POST | `/players/{id}/watchlist/{ticker}` | Add ticker |
| DELETE | `/players/{id}/watchlist/{ticker}` | Remove ticker |

### Prices & Analytics
| Method | Path | Description |
|---|---|---|
| GET | `/prices/{ticker}` | Current price |
| GET | `/prices/{ticker}/history` | Raw OHLCV history |
| GET | `/prices/{ticker}/stats` | 24h open/high/low/close/volume/change |
| GET | `/prices/{ticker}/candles` | Resampled OHLCV (multi-timeframe) |
| GET | `/prices/{ticker}/indicators` | All technical indicators |
| GET | `/competitions/{code}/orderbook/{ticker}` | Bid/ask depth |
| GET | `/competitions/{code}/screener` | Asset universe screener |
| WS | `/ws/{code}` | WebSocket: prices + leaderboard stream |

---

## Near-Future Tasks

### Frontend Setup
- [ ] Scaffold `frontend/` with Next.js App Router + TypeScript
- [ ] Decide styling: Tailwind (recommended for dark finance aesthetic)
- [ ] Home page: "Create lobby" form + "Join with code" input
- [ ] `lib/api.ts` typed fetch wrappers for all backend endpoints
- [ ] Dynamic route `app/[code]/page.tsx` — competition room shell

### UI: Dashboard
- [ ] Portfolio summary: total value, cash, open positions table
- [ ] Trade form: ticker, buy/sell, quantity, order type
- [ ] P&L chart over time using `GET /players/{id}/history` (Recharts)
- [ ] Leaderboard panel consuming WS stream
- [ ] Order history log

### CI/CD (extend)
- [ ] Add ESLint + `tsc --noEmit` to `ci.yml` once frontend exists

---

## Open Decisions

| Decision | Status |
|---|---|
| Default starting balance | $10,000 (decided) |
| Styling approach | Tailwind recommended; decide at frontend setup |
| Lobby code expiry | Not implemented; lobby state never expires currently |
| Timezone | UTC everywhere (in use) |
| Hosting target | Fly.io / Railway / self-hosted — not decided |
| Partial limit order fills | Deferred to V1+ |
