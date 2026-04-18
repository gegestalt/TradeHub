# TradeHub — Project Specification

## Overview

**TradeHub** is a portfolio management competition platform for friend groups. Players join a competition via a lobby code (no registration required), each start with the same virtual capital, and compete to achieve the best portfolio performance over a fixed time window. The platform supports multiple asset classes, pluggable live or historical data sources, realistic order types, and configurable scoring.

---

## Goals

- Make it easy for anyone in a group to spin up a competition in under 2 minutes
- Support realistic (but simulated) trading: fees, short selling, margin
- Work both offline (historical data) and online (live price feeds)
- Present rich dashboards so players can analyze their performance and compare with others
- Stay self-hostable and lightweight for personal/friend-group use

---

## Milestones

### MVP — Core Loop Works

The minimum viable product proves the core competition loop end-to-end.

| Feature | Details |
|---|---|
| Create competition | Name, duration, starting balance, asset universe, data source (online/offline) |
| Join via lobby code | 6-character alphanumeric code; player enters a display name — no account needed |
| Asset classes | Stocks + Crypto only |
| Data source | Offline only (bundled historical CSV/JSON files) |
| Order type | Market orders only (instant fill at current price) |
| Trading fees | Flat configurable % per trade (e.g., 0.1%) |
| Leaderboard | Ranked by total portfolio value; updates on each trade |
| Dashboard | Cash balance, open positions table, buy/sell form, leaderboard panel |
| Competition states | `lobby` → `active` → `ended` |

---

### V1 — Full Feature Parity

Expands asset support, data sources, order types, and UI richness.

| Feature | Details |
|---|---|
| All asset classes | Stocks, Bonds, Crypto, Commodities, Forex |
| Online data adapters | yfinance (stocks/bonds/commodities), CoinGecko (crypto), exchangerate.host (forex) |
| Offline data adapters | User-supplied or bundled CSV/JSON with OHLCV data |
| Realistic order types | Market order, Limit order, Stop-loss order |
| Short selling | Players can open short positions; P&L inverted |
| Configurable scoring | Competition creator chooses: **Total Portfolio Value** or **Sharpe Ratio** |
| Rich dashboard | P&L chart over time, asset allocation pie, trade history table, per-asset sparklines |
| Spectator mode | Shareable read-only URL showing live leaderboard (no login required) |
| Competition lifecycle | Creator can set start/end datetime or trigger manually |

---

### V2 — Depth & Analysis

Adds advanced trading mechanics and post-competition tooling.

| Feature | Details |
|---|---|
| Leverage / margin | Players can borrow up to a configurable multiplier; forced liquidation if margin is breached |
| Replay / post-mortem | Step through competition timeline; see each player's portfolio state and orders at any moment |
| Multiple competitions | Host can run several competitions simultaneously |
| Competition templates | Pre-configured setups (e.g., "Crypto Chaos: crypto only, 1 week, $10k, 2x max leverage") |
| Portfolio analytics | Max drawdown, annualized volatility, win rate per asset, best/worst trade |

---

### Later

Low priority enhancements that add polish or convenience but are not core.

- Scheduled / recurring competitions (auto-start on a cron schedule)
- In-competition commentary feed (trade announcement notifications)
- Mobile-responsive UI pass
- Export competition results to CSV or PDF

---

### Not In Scope

| Item | Reason |
|---|---|
| Real money / brokerage integration | Legal, regulatory, and complexity concerns |
| Full user registration & persistent profiles | Invite-only lobby model is sufficient for friend groups |
| Public SaaS / multi-tenant hosting | Designed for self-hosted personal use |
| AI / ML trade suggestions | Out of scope for this product |
| Social features (follow, friends list) | Lobby codes replace the need for a social graph |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Browser (Next.js)                    │
│   Dashboard  │  Leaderboard  │  Trade Form  │  Spectator    │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP / SSE
┌──────────────────────────▼──────────────────────────────────┐
│                      FastAPI Backend                        │
│  /competitions  /players  /orders  /portfolio  /prices      │
│                                                             │
│  ┌──────────────┐  ┌─────────────┐  ┌──────────────────┐   │
│  │ Competition  │  │  Order      │  │  Data Adapter    │   │
│  │ Service      │  │  Engine     │  │  (Online/Offline)│   │
│  └──────────────┘  └─────────────┘  └──────────────────┘   │
└──────────────────────────┬──────────────────────────────────┘
                           │
        ┌──────────────────┴───────────────────┐
        │                                      │
┌───────▼───────┐                   ┌──────────▼─────────┐
│  SQLite / PG  │                   │  Price Data        │
│  (app state)  │                   │  Online: yfinance  │
└───────────────┘                   │  Offline: CSV/JSON │
                                    └────────────────────┘
```

---

## Data Model

### Competition
| Field | Type | Notes |
|---|---|---|
| id | UUID | Primary key |
| name | string | Display name |
| lobby_code | string(6) | Join code, unique |
| starting_balance | decimal | Same for all players |
| start_at | datetime | Competition start |
| end_at | datetime | Competition end |
| state | enum | `lobby`, `active`, `ended` |
| asset_universe | string[] | Allowed ticker symbols or asset classes |
| data_source | enum | `online`, `offline` |
| scoring_method | enum | `total_value`, `sharpe_ratio` |
| fee_pct | decimal | Per-trade fee percentage (e.g., 0.001) |
| max_leverage | decimal | 1.0 = no leverage |
| allow_shorts | bool | Whether short positions are allowed |

### Player
| Field | Type | Notes |
|---|---|---|
| id | UUID | Primary key |
| competition_id | UUID | FK → Competition |
| display_name | string | Guest name chosen at join |
| cash_balance | decimal | Current available cash |
| joined_at | datetime | |
| spectator | bool | True = read-only spectator |

### Order
| Field | Type | Notes |
|---|---|---|
| id | UUID | Primary key |
| player_id | UUID | FK → Player |
| ticker | string | Asset symbol (e.g., AAPL, BTC-USD) |
| order_type | enum | `market`, `limit`, `stop_loss` |
| side | enum | `buy`, `sell` (sell on short = open short) |
| quantity | decimal | Number of units |
| limit_price | decimal | Null for market orders |
| stop_price | decimal | Null unless stop_loss |
| status | enum | `pending`, `filled`, `cancelled` |
| fill_price | decimal | Actual execution price |
| fill_at | datetime | |
| fee_paid | decimal | |

### Position
| Field | Type | Notes |
|---|---|---|
| id | UUID | Primary key |
| player_id | UUID | FK → Player |
| ticker | string | |
| quantity | decimal | Negative = short |
| avg_entry_price | decimal | Volume-weighted average cost |
| opened_at | datetime | |

### PriceSnapshot
| Field | Type | Notes |
|---|---|---|
| id | UUID | Primary key |
| competition_id | UUID | FK → Competition |
| ticker | string | |
| price | decimal | |
| recorded_at | datetime | |
| source | enum | `online`, `offline` |

---

## API Surface (REST + SSE)

### Competitions
| Method | Path | Description |
|---|---|---|
| POST | `/competitions` | Create a new competition |
| GET | `/competitions/{code}` | Get competition details by lobby code |
| POST | `/competitions/{code}/start` | Manually start competition |
| GET | `/competitions/{code}/leaderboard` | Current rankings |
| GET | `/competitions/{code}/leaderboard/stream` | SSE stream for live leaderboard |

### Players
| Method | Path | Description |
|---|---|---|
| POST | `/competitions/{code}/join` | Join as named guest; returns player token |
| GET | `/players/{id}/portfolio` | Full portfolio state |
| GET | `/players/{id}/history` | P&L over time snapshots |

### Orders
| Method | Path | Description |
|---|---|---|
| POST | `/players/{id}/orders` | Place an order |
| GET | `/players/{id}/orders` | List orders |
| DELETE | `/players/{id}/orders/{order_id}` | Cancel a pending order |

### Prices
| Method | Path | Description |
|---|---|---|
| GET | `/prices/{ticker}` | Current price for a ticker |
| GET | `/prices/{ticker}/history` | OHLCV history |

---

## Data Source Adapter Interface

The backend uses an adapter pattern so competitions can swap between live and historical data without changing business logic.

```python
class DataAdapter(Protocol):
    def get_price(self, ticker: str, at: datetime | None = None) -> Decimal: ...
    def get_ohlcv(self, ticker: str, start: datetime, end: datetime) -> list[OHLCV]: ...
    def list_tickers(self, asset_class: AssetClass) -> list[str]: ...

class OnlineDataAdapter:
    """Uses yfinance, CoinGecko, exchangerate.host."""
    ...

class OfflineDataAdapter:
    """Reads from local CSV/JSON files in /data directory."""
    ...
```

Competitions store their `data_source` setting; the backend injects the appropriate adapter at runtime.

---

## Key Design Decisions & Assumptions

1. **No auth required.** Players identify by a short-lived session token issued on join. The token is stored in localStorage. Lost token = rejoin under a new name (by design for simplicity).

2. **Competition creator is just the first player.** No separate host role in the DB; the first joiner holds the `creator` flag and can start/configure the competition.

3. **Prices are snapshotted periodically** (e.g., every 60s for active competitions) and stored as `PriceSnapshot` records. Order fills reference these snapshots, not live prices, to ensure consistency and support replay.

4. **Offline data ships with the app.** A `/data` directory in the repo contains historical OHLCV CSVs for a default set of tickers (S&P 500 top 20, BTC, ETH, gold, EUR/USD). Users can add their own.

5. **Sharpe Ratio scoring** requires at least daily portfolio value snapshots. The backend computes it at competition end over the full timeline.

6. **Spectator URLs** expose only leaderboard and aggregate portfolio data — individual trade details are private to each player.

---

## Open Questions

- What default starting balance should the app suggest? ($10,000 is a common paper trading default)
- Should the lobby code expire if no one joins within N hours?
- Should partially filled limit orders be supported in MVP or deferred to V1?
- What timezone should competition start/end times use? (UTC recommended)
