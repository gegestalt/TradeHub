    # CLAUDE.md — TradeHub

    > Draft. Update each section as the codebase grows.

    ---

    ## Architecture Overview

    ### Data Flow

    ```
    Browser (Next.js)
    └─ HTTP/SSE → FastAPI backend
                    ├─ CompetitionService   — lifecycle, scoring
                    ├─ OrderEngine          — fill logic, fee calc, short/margin
                    ├─ DataAdapter          — price resolution (online | offline)
                    └─ SQLite / PostgreSQL  — persistent state
                        ├─ Competition
                        ├─ Player
                        ├─ Order
                        ├─ Position
                        └─ PriceSnapshot
    ```

    Player tokens (issued at join) are stored in browser `localStorage`. No server-side sessions.

    ### Planned Directory Tree

    ```
    TradeHub/
    ├── CLAUDE.md                  # This file
    ├── project_spec.md            # Full product spec and milestones
    ├── .env.example               # Environment variable template
    ├── .gitignore
    │
    ├── backend/                   # FastAPI app (Python)
    │   ├── main.py                # App entrypoint, router registration
    │   ├── config.py              # Settings loaded from .env
    │   ├── database.py            # SQLAlchemy engine + session factory
    │   ├── models/                # SQLAlchemy ORM models
    │   │   ├── competition.py
    │   │   ├── player.py
    │   │   ├── order.py
    │   │   ├── position.py
    │   │   └── price_snapshot.py
    │   ├── routers/               # FastAPI route handlers
    │   │   ├── competitions.py
    │   │   ├── players.py
    │   │   ├── orders.py
    │   │   └── prices.py
    │   ├── services/              # Business logic (no HTTP concerns)
    │   │   ├── competition.py     # Lifecycle, scoring, leaderboard
    │   │   └── order_engine.py    # Fill logic, fees, shorts, margin
    │   ├── data_adapters/         # Pluggable price data sources
    │   │   ├── base.py            # DataAdapter Protocol definition
    │   │   ├── online.py          # yfinance, CoinGecko, exchangerate.host
    │   │   └── offline.py         # CSV/JSON reader from /data
    │   └── tests/                 # pytest tests
    │       ├── test_order_engine.py
    │       └── test_competition.py
    │
    ├── frontend/                  # Next.js app (TypeScript)
    │   ├── app/                   # App Router pages
    │   │   ├── page.tsx           # Home / create or join competition
    │   │   ├── [code]/            # Competition room by lobby code
    │   │   │   ├── page.tsx       # Dashboard (positions, trade form)
    │   │   │   └── spectate/
    │   │   │       └── page.tsx   # Read-only leaderboard view
    │   ├── components/
    │   │   ├── ui/                # Primitive components (buttons, inputs)
    │   │   ├── charts/            # Recharts wrappers (PnlChart, AllocationPie)
    │   │   ├── Leaderboard.tsx
    │   │   ├── TradeForm.tsx
    │   │   └── PositionsTable.tsx
    │   ├── lib/
    │   │   ├── api.ts             # Typed fetch wrappers for backend endpoints
    │   │   └── sse.ts             # SSE client hook for live leaderboard
    │   └── types/                 # Shared TypeScript types mirroring backend models
    │
    └── data/                      # Bundled offline OHLCV data (CSV/JSON)
        └── .gitkeep               # User-supplied CSVs are gitignored
    ```

    ---

    ## Design Style Guide

    ### Visual Style
    - **Dark finance aesthetic** — dark background, muted grays, accent color for gains (green) / losses (red)
    - Charts use **Recharts**; keep them minimal — no decorative gridlines, clean tooltips
    - Typography: monospace for numbers and tickers; sans-serif for everything else
    - Mobile-responsive is a "Later" milestone — don't optimize for it prematurely

    ### Component Patterns (Frontend)
    - Colocate component styles with the component (CSS Modules or Tailwind — decide at project start)
    - `components/ui/` holds only dumb, stateless primitives
    - Data fetching lives in page-level components; pass data down as props
    - SSE state goes through a custom hook in `lib/sse.ts`; don't inline it in components
    - Don't create a new component for a one-off use — inline it first, extract when used 3+ times

    ### Tech Stack
    | Layer | Choice |
    |---|---|
    | Frontend | Next.js (App Router) + TypeScript |
    | Styling | TBD at project start (Tailwind or CSS Modules) |
    | Charts | Recharts |
    | Backend | FastAPI + Python 3.11+ |
    | ORM | SQLAlchemy (async) |
    | DB | SQLite (dev/MVP) → PostgreSQL (prod) |
    | Migrations | Alembic |
    | Realtime | Server-Sent Events (SSE) |
    | Testing | pytest (backend), Vitest (frontend) |

    ---

    ## Product & UX Guidelines

    ### Core UX Principles
    1. **Zero friction to start** — creating or joining a game must take under 60 seconds
    2. **No registration wall** — never ask for email or password; lobby codes are the identity mechanism
    3. **Optimistic UI** — trade submissions should feel instant; reconcile with backend silently
    4. **Legible numbers** — always format currency with commas and 2 decimal places; show % change with sign (`+3.2%`, `-1.1%`)

    ### Copy Tone
    - Casual and direct — this is a game between friends, not a Bloomberg terminal
    - Use plain language: "Your balance", not "Net Asset Value"
    - Error messages should explain what to do, not just what went wrong

    ### UI Zones
    | Zone | Purpose |
    |---|---|
    | Top bar | Competition name, time remaining, player name |
    | Left panel | Portfolio summary: total value, cash, open positions |
    | Center | Primary chart (P&L over time) or trade form |
    | Right panel | Leaderboard |
    | Bottom | Trade history / order log |

    ---

    ## Constraints & Policies

    ### Security — MUST Follow
    - **Never log player tokens** in plaintext in server logs
    - **Validate all ticker symbols** against the competition's `asset_universe` before placing an order — never pass raw user input to a data adapter or DB query
    - **Rate-limit order submission** per player (e.g., 10 orders/minute) to prevent abuse
    - **CORS** must be explicitly configured — do not use `allow_origins=["*"]` in production
    - **No real money** — never integrate payment processors or real brokerage APIs

    ### Code Quality
    - Backend: type-annotate all function signatures; run `ruff` for linting
    - Frontend: strict TypeScript — no `any` types; run `tsc --noEmit` before committing
    - Keep business logic in `services/` — routers should only parse requests and call services
    - Services must not import from `routers/`

    ### Dependencies
    - Prefer stdlib or already-included packages before adding new ones
    - New backend dependency → update `requirements.txt`; new frontend dependency → update `package.json`
    - Avoid alpha/beta packages for core functionality

    ---

    ## Repository Etiquette

    ### Branching
    - `master` — always deployable; never commit directly for non-trivial changes
    - `feat/<short-name>` — new features (e.g., `feat/order-engine`)
    - `fix/<short-name>` — bug fixes
    - `chore/<short-name>` — tooling, config, deps (no product changes)

    ### Git Workflow for Major Changes
    1. Cut a feature branch from `master`
    2. Make small, focused commits
    3. Open a PR — describe what changed and why
    4. Squash-merge into `master` when approved

    ### Commits
    - Format: `<type>: <short imperative summary>` (e.g., `feat: add limit order support`)
    - Types: `feat`, `fix`, `chore`, `refactor`, `test`, `docs`
    - Keep subject line ≤ 72 characters
    - Add `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>` when Claude authors the commit

    ### Pull Requests
    - Title follows the same format as commit messages
    - Body: what changed, why, how to test it
    - Link to the relevant GitHub issue if one exists

    ### Before Pushing
    - Backend: `ruff check .` passes, `pytest` passes
    - Frontend: `tsc --noEmit` passes, `npm run lint` passes
    - `.env` is NOT staged

    ---

    ## Frequently Used Commands

    ```bash
    # ── Backend ────────────────────────────────────────────────
    cd backend
    uv run uvicorn main:app --reload --reload-dir . # Start dev server (port 8000)
    pytest                                        # Run all tests
    ruff check .                                  # Lint
    alembic upgrade head                          # Apply DB migrations
    alembic revision --autogenerate -m "message" # Generate new migration

    # ── Frontend ───────────────────────────────────────────────
    cd frontend
    npm run dev                                   # Start dev server (port 3000)
    npm run build                                 # Production build
    npm run lint                                  # ESLint
    npx tsc --noEmit                              # Type-check without emitting

    # ── Full stack ─────────────────────────────────────────────
    # (from repo root, once docker-compose.yml exists)
    docker compose up --build
    ```

    ---

    ## Testing

    ### Manual Testing Checklist (MVP)
    - [ ] Create a competition → lobby code is shown
    - [ ] Join from a second browser tab using the code
    - [ ] Start the competition as creator
    - [ ] Place a market buy order; confirm balance decreases and position appears
    - [ ] Place a market sell order; confirm position closes and cash updates
    - [ ] Verify leaderboard updates after each trade
    - [ ] End competition; verify final rankings shown

    ### Unit Tests (Backend — pytest)
    - `test_order_engine.py` — fill price calculation, fee deduction, short P&L, margin liquidation
    - `test_competition.py` — lobby code generation uniqueness, state transitions, scoring (total value + Sharpe)
    - Each test uses an in-memory SQLite DB — no external services

    ### Frontend Tests (Vitest)
    - Pure utility functions in `lib/` (number formatting, SSE reconnect logic)
    - No component snapshot tests unless there's a strong reason

    ---

    ## Documentation

    | Document | Path | Purpose |
    |---|---|---|
    | Product spec & milestones | [project_spec.md](project_spec.md) | Source of truth for features, data model, API surface |
    | Environment variables | [.env.example](.env.example) | All configurable env vars with descriptions |
    | Changelog | [CHANGELOG.md](CHANGELOG.md) | Notable changes per release |
    | Project status | [project_status.md](project_status.md) | Current state, task queue, open decisions |
    | This file | [CLAUDE.md](CLAUDE.md) | Architecture, conventions, and workflows for Claude |

    - Update the Documentation after major milestones and additions to the project.
    - Refresh memory after successful major adition and before new major addition
