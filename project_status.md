# Project Status

> Last updated: 2026-04-19
> Current phase: **Backend complete — CI/CD in place**

---

## Where We Left Off

Full backend is merged to `main`. CI/CD pipeline (`feat/ci-cd`) is being merged.
Backend is lint-clean (`ruff`) and all 32 tests pass. Frontend work has not started.

---

## Current State

| Area | Status | Notes |
|---|---|---|
| Product spec | Done | `project_spec.md` — milestones, data model, API surface |
| Repo & git | Done | `github.com/gegestalt/TradeHub` on `main` |
| Env config | Done | `.env.example` covers all adapters and services |
| CLAUDE.md | Done | Architecture, conventions, commands, test strategy |
| Changelog | Done | `CHANGELOG.md` — Keep a Changelog format |
| Backend skeleton | Done | FastAPI app, models, DB, routers, lifespan |
| Order engine | Done | Fill logic, fees, VWAC, short selling, per-player lock |
| Data adapters | Done | OfflineAdapter (CSV/JSON), OnlineAdapter (yfinance), MockAdapter |
| Competition API | Done | Create, join, start, leaderboard endpoints |
| Tests (backend) | Done | 32 pytest tests: unit, property-based (Hypothesis), concurrency |
| Audit logging | Done | Structured JSON to `tradehub.audit` logger |
| Metrics | Done | In-process metrics exposed at `GET /metrics` |
| CI/CD | Done | `.github/workflows/ci.yml` — ruff + pytest on push/PR |
| Frontend skeleton | Not started | Next.js app, layout, basic pages |
| Leaderboard SSE | Not started | Live score stream |
| UI: Dashboard | Not started | Charts, positions table, trade form |
| Tests (frontend) | Not started | Vitest |

---

## Milestone Progress

```
[MVP  ]  ████████░░  ~60%  Backend complete; frontend not started
[V1   ]  ░░░░░░░░░░   0%   Full asset classes, order types, spectator mode
[V2   ]  ░░░░░░░░░░   0%   Margin, replay, analytics
[Later]  ░░░░░░░░░░   0%   Scheduled comps, commentary, mobile polish
```

---

## Near-Future Tasks

### Agent: Frontend Setup
Stand up the Next.js skeleton with a home page and routing.

- [ ] Scaffold `frontend/` with Next.js App Router + TypeScript
- [ ] Decide and configure styling (Tailwind vs CSS Modules)
- [ ] Home page: "Create competition" form + "Join with code" input
- [ ] Dynamic route `app/[code]/page.tsx` renders competition room shell
- [ ] `lib/api.ts` typed fetch wrapper pointing at `NEXT_PUBLIC_API_URL`

### Agent: Leaderboard SSE
Wire up the live leaderboard stream.

- [ ] `GET /competitions/{code}/leaderboard/stream` SSE endpoint
- [ ] Frontend hook `useLeaderboard(code)` consuming the stream
- [ ] Reconnection logic with exponential backoff

### Agent: UI Dashboard
Player-facing trading interface.

- [ ] Portfolio summary: total value, cash, open positions
- [ ] Trade form: ticker input, buy/sell, quantity, submit
- [ ] Positions table with live P&L
- [ ] P&L chart over time (Recharts)
- [ ] Order history / fill log

### Agent: CI/CD (extend)
- [ ] Add ESLint + `tsc --noEmit` checks to `ci.yml` once frontend exists
- [ ] Add `deploy.yml` target once hosting is decided

---

## Open Decisions

| Decision | Options | Priority |
|---|---|---|
| Default starting balance | $10,000 (confirmed in backend) | Decided |
| Styling approach | Tailwind vs CSS Modules | Before frontend setup |
| Lobby code expiry | Expire after N hours with no join, or never | Before MVP |
| Timezone handling | UTC everywhere (in use) | Decided |
| Partial limit order fills | Support in MVP or defer to V1 | Before order engine |
| Hosting target | Fly.io / Railway / self-hosted | Before deploy.yml |
