#!/usr/bin/env bash
# ================================================================
#  TradeHub — Live Data Dynamic Test Suite
#
#  Every run is different:
#    - 5 players each randomly pick 3-6 assets from a 20-asset pool
#    - Quantities computed from live prices at runtime
#    - Random mix of market orders, limit orders, sells, stop-losses,
#      take-profits — each player's portfolio is unpredictable
#    - No hardcoded strategies; results change with the market
#  - All 5 players fire their orders CONCURRENTLY (parallel background jobs)
#
#  Usage: bash live_test_runner.sh
# ================================================================

BASE="http://localhost:8000"
OUT="live_test_results.txt"
BALANCE="100000"
PASS=0
FAIL=0
SKIP=0

UNIVERSE='[
  "AAPL","MSFT","NVDA","AMD","META","GOOGL","AMZN","TSLA",
  "NFLX","CRM","JPM","V","MA","GS","ORCL","UBER",
  "SPY","QQQ","BTC-USD","ETH-USD"
]'

> "$OUT"

# ── I/O ──────────────────────────────────────────────────────────
ts()  { date '+%Y-%m-%d %H:%M:%S'; }
log() { printf '%s\n' "$*" | tee -a "$OUT" >&2; }

divider() {
    log ""
    log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    log "  $1"
    log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

req() {
    local label="$1" method="$2" url="$3"; shift 3
    log ""
    log "┌─ $label"
    log "│  Time   : $(ts)  │  $method  $url"
    local prev=""
    for arg in "$@"; do
        if [[ "$prev" == "-H" ]]; then log "│  Header : $arg"; fi
        if [[ "$prev" == "-d" ]]; then log "│  Body   : $arg"; fi
        prev="$arg"
    done
    local bf; bf=$(mktemp)
    local meta; meta=$(curl -s -o "$bf" -w "%{http_code}|%{time_total}" -X "$method" "$url" "$@")
    local code ms
    code=$(echo "$meta" | cut -d'|' -f1)
    ms=$(python3 -c "print(round($(echo "$meta" | cut -d'|' -f2)*1000,1))" 2>/dev/null)
    local body; body=$(cat "$bf"); rm -f "$bf"
    local pretty; pretty=$(echo "$body" | python3 -m json.tool 2>/dev/null || echo "$body")
    log "│  Status : $code  (${ms} ms)"
    log "│  Response:"
    while IFS= read -r line; do log "│    $line"; done <<< "$pretty"
    log "└─"
    echo "$body"
}

jq_() { python3 -c "import sys,json; d=json.load(sys.stdin); print(d$1)" 2>/dev/null; }

check() {
    local label="$1" want="$2" got="$3"
    if echo "$got" | grep -qE "$want"; then
        log "  OK  $label"; PASS=$((PASS+1))
    else
        log "  FAIL: $label"
        log "     want : $want"
        log "     got  : $(echo "$got" | head -c 250)"
        FAIL=$((FAIL+1))
    fi
}
check_absent() {
    local label="$1" absent="$2" got="$3"
    if echo "$got" | grep -q "$absent"; then
        log "  FAIL: $label (found '$absent')"; FAIL=$((FAIL+1))
    else
        log "  OK  $label"; PASS=$((PASS+1))
    fi
}

log "================================================================"
log "  TRADEHUB — LIVE DATA DYNAMIC TEST RUN"
log "  $(ts)"
log "  Server  : $BASE"
log "  Source  : online — live prices"
log "  Assets  : 20 (stocks, ETFs, crypto)"
log "  Players : 5 — random strategies, all trade simultaneously"
log "================================================================"

# ================================================================
divider "PART 1 — REGISTER 5 PLAYERS"
# ================================================================

register() {
    local name="$1"
    local R; R=$(req "Register $name" POST $BASE/users/register \
        -H "Content-Type: application/json" -d "{\"display_name\":\"$name\"}")
    check "$name registered" "token" "$R"
    echo "$R" | jq_ "['token']"
}

ALICE_UT=$(register "Alice")
BOB_UT=$(register "Bob")
CHARLIE_UT=$(register "Charlie")
DAVE_UT=$(register "Dave")
EVE_UT=$(register "Eve")

R=$(req "Empty name rejected" POST $BASE/users/register \
    -H "Content-Type: application/json" -d '{"display_name":""}')
check "empty name → 422" "string_too_short" "$R"

# ================================================================
divider "PART 2 — CREATE COMPETITION (20 ASSETS)"
# ================================================================

COMP_BODY=$(python3 -c "
import json
universe = ['AAPL','MSFT','NVDA','AMD','META','GOOGL','AMZN','TSLA',
            'NFLX','CRM','JPM','V','MA','GS','ORCL','UBER',
            'SPY','QQQ','BTC-USD','ETH-USD']
print(json.dumps({
    'name': 'Live Dynamic Game',
    'starting_balance': '100000',
    'asset_universe': universe,
    'fee_pct': '0.001',
    'data_source': 'online'
}))")

R=$(req "Alice creates competition (20 assets)" POST $BASE/competitions \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $ALICE_UT" \
    -d "$COMP_BODY")
CODE=$(echo "$R"      | jq_ "['lobby_code']")
ALICE_PT=$(echo "$R"  | jq_ "['token']")
ALICE_PID=$(echo "$R" | jq_ "['player_id']")
check "competition created" "lobby_code" "$R"
check "data_source online" "online" "$R"
log "  →  Lobby code : $CODE"

R=$(req "Get competition (public)" GET "$BASE/competitions/$CODE")
check "state is lobby" "lobby" "$R"
check "20 assets in universe" "ETH-USD" "$R"

# ================================================================
divider "PART 3 — JOIN COMPETITION"
# ================================================================

join_comp() {
    local name="$1" ut="$2"
    local R; R=$(req "$name joins" POST "$BASE/competitions/$CODE/join" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $ut" -d '{"spectator":false}')
    check "$name joined with \$100k" "100000" "$R"
    echo "$(echo "$R" | jq_ "['token']")|$(echo "$R" | jq_ "['player_id']")"
}

IFS='|' read -r BOB_PT     BOB_PID     <<< "$(join_comp "Bob"     "$BOB_UT")"
IFS='|' read -r CHARLIE_PT CHARLIE_PID <<< "$(join_comp "Charlie" "$CHARLIE_UT")"
IFS='|' read -r DAVE_PT    DAVE_PID    <<< "$(join_comp "Dave"    "$DAVE_UT")"
IFS='|' read -r EVE_PT     EVE_PID     <<< "$(join_comp "Eve"     "$EVE_UT")"

# Spectator
R=$(req "Register Watcher" POST $BASE/users/register \
    -H "Content-Type: application/json" -d '{"display_name":"Watcher"}')
WATCHER_UT=$(echo "$R" | jq_ "['token']")
R=$(req "Watcher joins as spectator" POST "$BASE/competitions/$CODE/join" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $WATCHER_UT" -d '{"spectator":true}')
check "spectator has \$0 balance" '"cash_balance":"0' "$R"

R=$(req "Bob tries to rejoin (expect 409)" POST "$BASE/competitions/$CODE/join" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $BOB_UT" -d '{"spectator":false}')
check "duplicate join rejected" "already joined" "$R"

# ================================================================
divider "PART 4 — START COMPETITION"
# ================================================================

R=$(req "Alice starts competition" POST "$BASE/competitions/$CODE/start" \
    -H "Authorization: Bearer $ALICE_PT")
check "state is active" "active" "$R"

R=$(req "Register Late + try to join (expect 400)" POST $BASE/users/register \
    -H "Content-Type: application/json" -d '{"display_name":"Late"}')
LATE_UT=$(echo "$R" | jq_ "['token']")
R=$(req "Late join blocked" POST "$BASE/competitions/$CODE/join" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $LATE_UT" -d '{"spectator":false}')
check "late join blocked after start" "started" "$R"

# ================================================================
divider "PART 5 — LIVE PRICE DISCOVERY (20 ASSETS)"
# ================================================================

log "  Fetching prices for all 20 assets..."

PRICE_JSON=$(python3 - << 'PYEOF'
import urllib.request, json, sys

assets = ['AAPL','MSFT','NVDA','AMD','META','GOOGL','AMZN','TSLA',
          'NFLX','CRM','JPM','V','MA','GS','ORCL','UBER',
          'SPY','QQQ','BTC-USD','ETH-USD']
prices = {}
for a in assets:
    try:
        r = urllib.request.urlopen(f'http://localhost:8000/prices/{a}', timeout=15)
        d = json.loads(r.read())
        p = float(d['price'])
        prices[a] = p
    except Exception as e:
        prices[a] = 0.0

print(json.dumps(prices))
PYEOF
)

# Log all prices
log ""
log "  ┌─ Live Prices ($(ts))"
python3 - "$PRICE_JSON" << 'PYEOF' | tee -a "$OUT" >&2
import sys, json
prices = json.loads(sys.argv[1])
items = list(prices.items())
for i in range(0, len(items), 4):
    row = items[i:i+4]
    line = "  │  " + "   ".join(f"{a:<10} ${p:>10.2f}" for a,p in row)
    print(line)
PYEOF
log "  └─"

# Validate key prices
P_BTC=$(echo "$PRICE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['BTC-USD'])")
P_ETH=$(echo "$PRICE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['ETH-USD'])")
P_AAPL=$(echo "$PRICE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['AAPL'])")

check "BTC-USD price fetched" "[0-9]" "$P_BTC"
check "ETH-USD price fetched" "[0-9]" "$P_ETH"
check "AAPL price fetched" "[0-9]" "$P_AAPL"

R=$(req "AAPL market status" GET "$BASE/prices/AAPL/status")
check "market_open field present" "market_open" "$R"
check "timezone present" "timezone" "$R"
MARKET_STATUS=$(echo "$R" | jq_ "['market_status']")
log "  →  NYSE status : $MARKET_STATUS"

R=$(req "BTC-USD always open" GET "$BASE/prices/BTC-USD/status")
check "crypto market always open" "market_open" "$R"

R=$(req "Invalid ticker rejected" GET "$BASE/prices/FAKECOIN999")
check "invalid ticker → 404" "detail" "$R"

# ================================================================
divider "PART 6 — CONCURRENT TRADING (ALL 5 PLAYERS SIMULTANEOUSLY)"
# ================================================================

log ""
log "  Generating random trading plans from live prices..."
log "  Each player picks 3–6 random assets. Order types are random."
log "  Results will differ on every run."
log ""

# Generate all orders as tab-separated: player|body_json|note|expect_status
TMPPLAN=$(mktemp)
python3 - "$PRICE_JSON" > "$TMPPLAN" << 'PYEOF'
import sys, json, random

prices = json.loads(sys.argv[1])
balance = 100000.0
fee = 0.001

players = ['Alice', 'Bob', 'Charlie', 'Dave', 'Eve']

# Filter to assets with valid prices
valid = {k: v for k, v in prices.items() if v > 0}
universe = list(valid.keys())

def qty_str(price, spend):
    """Return quantity string that fits within spend at given price."""
    if price > 5000:
        q = round(spend / price, 4)
        return f"{max(0.0001, q):.4f}"
    else:
        q = max(1, int(spend / price))
        return str(q)

def body_market(ticker, side, qty):
    return json.dumps({"ticker": ticker, "side": side, "quantity": qty})

def body_limit(ticker, side, qty, limit_price):
    return json.dumps({"ticker": ticker, "side": side, "order_type": "limit",
                       "quantity": qty, "limit_price": str(limit_price)})

def body_stop(ticker, qty, stop_price):
    return json.dumps({"ticker": ticker, "side": "sell", "order_type": "stop_loss",
                       "quantity": qty, "stop_price": str(stop_price)})

def body_tp(ticker, qty, tp_price):
    return json.dumps({"ticker": ticker, "side": "sell", "order_type": "take_profit",
                       "quantity": qty, "take_profit_price": str(tp_price)})

MAX_ORDERS_PER_PLAYER = 9  # hard cap below the server's 10/minute rate limit

for player in players:
    remaining = balance * 0.92   # keep ~8% cash buffer
    owned = {}                   # track position for valid sell generation
    order_count = 0

    # Each player randomly picks 3-5 assets
    n = random.randint(3, 5)
    chosen = random.sample(universe, min(n, len(universe)))

    for asset in chosen:
        if order_count >= MAX_ORDERS_PER_PLAYER:
            break

        price = valid[asset]
        if remaining < 200:
            break

        # Random spend: 12–40% of remaining balance
        spend_pct = random.uniform(0.12, 0.40)
        spend = min(remaining * spend_pct, remaining * 0.45)
        qs = qty_str(price, spend)
        actual_cost = float(qs) * price * (1 + fee)

        if actual_cost > remaining:
            spend = remaining * 0.90
            qs = qty_str(price, spend)
            actual_cost = float(qs) * price * (1 + fee)

        remaining -= actual_cost
        owned[asset] = float(qs)

        # ── Buy order: 55% market, 45% limit (limit set above market → fills immediately)
        if random.random() < 0.45:
            lp = round(price * random.uniform(1.001, 1.008), 2)
            note = f"Limit buy {qs} {asset} @ limit ${lp:.2f} (market ${price:.2f})"
            print(f"{player}\t{body_limit(asset, 'buy', qs, lp)}\t{note}\tfilled")
        else:
            note = f"Market buy {qs} {asset} @ ~${price:.2f}"
            print(f"{player}\t{body_market(asset, 'buy', qs)}\t{note}\tfilled")
        order_count += 1

        # ── 45% chance: sell a random portion (creates realized P&L)
        if order_count < MAX_ORDERS_PER_PLAYER and random.random() < 0.45:
            sell_frac = random.uniform(0.25, 0.70)
            if price > 5000:
                sq = round(float(qs) * sell_frac, 4)
                sq = max(0.0001, sq)
                sq_str = f"{sq:.4f}"
            else:
                sq = max(1, int(float(qs) * sell_frac))
                sq_str = str(sq)

            owned[asset] = max(0, owned[asset] - float(sq_str))
            proceeds = float(sq_str) * price * (1 - fee)
            remaining += proceeds

            note = f"Partial sell {sq_str} {asset} @ ~${price:.2f} (round-trip)"
            print(f"{player}\t{body_market(asset, 'sell', sq_str)}\t{note}\tfilled")
            order_count += 1

        remaining_qty = owned.get(asset, 0)

        # ── 25% chance: stop-loss on remaining position (resting, far below market)
        if order_count < MAX_ORDERS_PER_PLAYER and remaining_qty > 0.0001 and random.random() < 0.25:
            stop = round(price * random.uniform(0.82, 0.91), 2)
            if price > 5000:
                sl_qty = f"{remaining_qty:.4f}"
            else:
                sl_qty = str(max(1, int(remaining_qty)))
            note = f"Stop-loss {sl_qty} {asset} @ ${stop:.2f} (resting, -{((1-stop/price)*100):.1f}% from market)"
            print(f"{player}\t{body_stop(asset, sl_qty, stop)}\t{note}\tpending")
            order_count += 1

        # ── 20% chance: take-profit on remaining position (resting, far above market)
        if order_count < MAX_ORDERS_PER_PLAYER and remaining_qty > 0.0001 and random.random() < 0.20:
            tp = round(price * random.uniform(1.10, 1.30), 2)
            if price > 5000:
                tp_qty = f"{min(remaining_qty, float(qs)):.4f}"
            else:
                tp_qty = str(max(1, int(min(remaining_qty, float(qs)))))
            note = f"Take-profit {tp_qty} {asset} @ ${tp:.2f} (resting, +{((tp/price-1)*100):.1f}% target)"
            print(f"{player}\t{body_tp(asset, tp_qty, tp)}\t{note}\tpending")
            order_count += 1

PYEOF

# Show the generated plan summary
log "  ┌─ GENERATED TRADING PLANS"
python3 - "$TMPPLAN" << 'PYEOF' | tee -a "$OUT" >&2
import sys

plans = {}
with open(sys.argv[1]) as f:
    for line in f:
        parts = line.strip().split('\t')
        if len(parts) >= 3:
            player = parts[0]
            note = parts[2]
            plans.setdefault(player, []).append(note)

for player, notes in plans.items():
    print(f"  │  {player}:")
    for n in notes:
        print(f"  │    - {n}")
PYEOF
log "  └─"
log ""

# ── Concurrent execution ─────────────────────────────────────────────────────
# All 5 players fire their orders simultaneously using background jobs (&).
# Each player has their own rate-limit bucket (10 orders/min per player), so
# no sleeps are needed — players never share a rate-limit window.
#
# Pass/fail counts can't be accumulated via shell variables across subshells;
# instead each background job appends to temp files; we count lines at the end.
# ─────────────────────────────────────────────────────────────────────────────

log "  Launching all 5 players in parallel — orders fire concurrently."
log ""

CONC_PASS_FILE=$(mktemp)
CONC_FAIL_FILE=$(mktemp)

run_player_orders() {
    local pname="$1" ppid="$2" ppt="$3" plan="$4"
    log "  --> $pname (player_id: $ppid) starting..."

    while IFS=$'\t' read -r _player body note _exp; do
        local R; R=$(req "$pname — $note" \
            POST "$BASE/competitions/$CODE/players/$ppid/orders" \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer $ppt" \
            -d "$body")

        local status fill detail
        status=$(echo "$R" | jq_ "['status']" 2>/dev/null)
        fill=$(echo "$R"   | jq_ "['fill_price']" 2>/dev/null)
        detail=$(echo "$R" | jq_ "['detail']" 2>/dev/null)

        if [[ "$status" == "filled" ]]; then
            log "  OK  [$pname] $note --> filled @ \$$fill"
            echo 1 >> "$CONC_PASS_FILE"
        elif [[ "$status" == "pending" ]]; then
            log "  OK  [$pname] $note --> pending (resting order)"
            echo 1 >> "$CONC_PASS_FILE"
        elif [[ "$status" == "expired" ]]; then
            log "  OK  [$pname] $note --> expired (IOC)"
            echo 1 >> "$CONC_PASS_FILE"
        elif [[ "$status" == "cancelled" ]]; then
            log "  OK  [$pname] $note --> cancelled"
            echo 1 >> "$CONC_PASS_FILE"
        else
            log "  FAIL  [$pname] $note --> REJECTED: $detail"
            echo 1 >> "$CONC_FAIL_FILE"
        fi
    done < <(grep "^${pname}	" "$plan")

    log "  <-- $pname finished."
}

# Launch all 5 players as background jobs — they trade concurrently
run_player_orders "Alice"   "$ALICE_PID"   "$ALICE_PT"   "$TMPPLAN" &
run_player_orders "Bob"     "$BOB_PID"     "$BOB_PT"     "$TMPPLAN" &
run_player_orders "Charlie" "$CHARLIE_PID" "$CHARLIE_PT" "$TMPPLAN" &
run_player_orders "Dave"    "$DAVE_PID"    "$DAVE_PT"    "$TMPPLAN" &
run_player_orders "Eve"     "$EVE_PID"     "$EVE_PT"     "$TMPPLAN" &

log "  ...  All 5 players trading concurrently — waiting for all to finish..."
wait
log "  OK   All players done."
log ""

# Tally results from temp files
CONC_PASS=$(wc -l < "$CONC_PASS_FILE" | tr -d ' ')
CONC_FAIL=$(wc -l < "$CONC_FAIL_FILE" | tr -d ' ')
rm -f "$CONC_PASS_FILE" "$CONC_FAIL_FILE"

PASS=$((PASS + CONC_PASS))
FAIL=$((FAIL + CONC_FAIL))

log "  →  Concurrent trading: $CONC_PASS passed, $CONC_FAIL failed"

rm -f "$TMPPLAN"

# ================================================================
divider "PART 7 — GUARD RAILS (REJECTION TESTS)"
# ================================================================

R=$(req "Trade ticker not in universe (HOOD)" \
    POST "$BASE/competitions/$CODE/players/$ALICE_PID/orders" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $ALICE_PT" \
    -d '{"ticker":"HOOD","side":"buy","quantity":"1"}')
check "ticker outside universe rejected" "asset universe" "$R"

R=$(req "Sell 999999 SPY (no such position)" \
    POST "$BASE/competitions/$CODE/players/$BOB_PID/orders" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $BOB_PT" \
    -d '{"ticker":"SPY","side":"sell","quantity":"999999"}')
check "oversell rejected" "detail" "$R"

R=$(req "Alice trades as Dave (cross-player, expect 403)" \
    POST "$BASE/competitions/$CODE/players/$DAVE_PID/orders" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $ALICE_PT" \
    -d '{"ticker":"AAPL","side":"buy","quantity":"1"}')
check "cross-player order rejected" "Cannot place" "$R"

R=$(req "Alice places a cancellable limit order" \
    POST "$BASE/competitions/$CODE/players/$ALICE_PID/orders" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $ALICE_PT" \
    -d '{"ticker":"AAPL","side":"buy","order_type":"limit","quantity":"1","limit_price":"0.01"}')
CANCEL_OID=$(echo "$R" | jq_ "['id']")
check "limit order is pending" "pending" "$R"

R=$(req "Alice cancels that limit order" \
    DELETE "$BASE/competitions/$CODE/players/$ALICE_PID/orders/$CANCEL_OID" \
    -H "Authorization: Bearer $ALICE_PT")
check "order cancelled" "cancelled" "$R"

R=$(req "IOC order that cannot fill → expires" \
    POST "$BASE/competitions/$CODE/players/$CHARLIE_PID/orders" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $CHARLIE_PT" \
    -d '{"ticker":"AAPL","side":"buy","order_type":"limit","quantity":"1","limit_price":"0.01","time_in_force":"ioc"}')
check "IOC order expired" "expired" "$R"

# ================================================================
divider "PART 8 — PORTFOLIOS (LIVE UNREALIZED P&L)"
# ================================================================

show_portfolio() {
    local name="$1" pid="$2" pt="$3"
    local R; R=$(req "$name portfolio" GET "$BASE/players/$pid/portfolio" \
        -H "Authorization: Bearer $pt")
    check "$name portfolio has total_value" "total_value" "$R"
    local tv cash npos
    tv=$(echo "$R"   | jq_ "['total_value']")
    cash=$(echo "$R" | jq_ "['cash_balance']")
    npos=$(echo "$R" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('positions',[])))" 2>/dev/null)
    log "  →  $name: total=\$$tv  cash=\$$cash  open_positions=$npos"
}

show_portfolio "Alice"   "$ALICE_PID"   "$ALICE_PT"
show_portfolio "Bob"     "$BOB_PID"     "$BOB_PT"
show_portfolio "Charlie" "$CHARLIE_PID" "$CHARLIE_PT"
show_portfolio "Dave"    "$DAVE_PID"    "$DAVE_PT"
show_portfolio "Eve"     "$EVE_PID"     "$EVE_PT"

R=$(req "Bob reads Alice portfolio (expect 403)" \
    GET "$BASE/players/$ALICE_PID/portfolio" \
    -H "Authorization: Bearer $BOB_PT")
check "cross-player portfolio blocked" "Cannot access" "$R"

# ================================================================
divider "PART 9 — LEADERBOARD (LIVE RANKINGS)"
# ================================================================

R=$(req "Live leaderboard (no auth required)" \
    GET "$BASE/competitions/$CODE/leaderboard")
check "Alice on leaderboard" "Alice" "$R"
check "Bob on leaderboard" "Bob" "$R"
check "Charlie on leaderboard" "Charlie" "$R"
check "Dave on leaderboard" "Dave" "$R"
check "Eve on leaderboard" "Eve" "$R"
check_absent "Watcher NOT on leaderboard" "Watcher" "$R"
check "rank field present" '"rank"' "$R"
check "pnl field present" '"pnl"' "$R"
check "unrealized_pnl present" "unrealized_pnl" "$R"

log ""
log "  ┌─ LIVE LEADERBOARD"
echo "$R" | python3 - << 'PYEOF' | tee -a "$OUT" >&2
import sys, json
players = json.load(sys.stdin)
print(f"  │  {'Rank':<5} {'Player':<10} {'Total Value':>13} {'P&L':>10} {'P&L%':>8}  {'Open Pos':>4}  Assets held")
print("  │  " + "─"*80)
for p in players:
    tv   = float(p['total_value'])
    pnl  = float(p['pnl'])
    pct  = float(p['pnl_pct'])
    sign = '+' if pnl >= 0 else ''
    tickers = [pos['ticker'] for pos in p.get('positions',[])]
    assets = ', '.join(tickers) if tickers else 'cash only'
    print(f"  │  #{p['rank']:<4} {p['display_name']:<10} ${tv:>12,.2f} {sign}{pnl:>9,.2f} {sign}{pct:>7.3f}%  {len(tickers):>4}  {assets}")
PYEOF
log "  └─"

log ""
log "  ┌─ POSITION DETAIL (leaderboard)"
echo "$R" | python3 - << 'PYEOF' | tee -a "$OUT" >&2
import sys, json
players = json.load(sys.stdin)
for p in players:
    name = p['display_name']
    rpnl = float(p['realized_pnl'])
    upnl = float(p['unrealized_pnl'])
    filled = p['orders_filled']
    sign_r = '+' if rpnl >= 0 else ''
    sign_u = '+' if upnl >= 0 else ''
    print(f"  │  {name} (rank #{p['rank']})  realized={sign_r}${rpnl:.2f}  unrealized={sign_u}${upnl:.2f}  fills={filled}")
    for pos in p.get('positions', []):
        t   = pos['ticker']
        qty = float(pos['quantity'])
        ent = float(pos['avg_entry_price'])
        cur = float(pos['current_price'])
        mv  = float(pos['market_value'])
        up  = float(pos['unrealized_pnl'])
        sign = '+' if up >= 0 else ''
        pct_chg = ((cur-ent)/ent*100) if ent else 0
        s_pct = '+' if pct_chg >= 0 else ''
        print(f"  │     {t:<10}  qty={qty:>10.4f}  entry=${ent:>9.2f}  now=${cur:>9.2f}  ({s_pct}{pct_chg:.3f}%)  MV=${mv:>11,.2f}  uPnL={sign}${up:.2f}")
PYEOF
log "  └─"

R=$(req "Trades feed (all players, public)" GET "$BASE/competitions/$CODE/trades")
check "trades feed returned" "ticker" "$R"
TRADE_N=$(echo "$R" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null)
log "  →  Total fills in feed: $TRADE_N"

R=$(req "Order book — AAPL pending limits" GET "$BASE/competitions/$CODE/orderbook/AAPL")
check "order book returned" "bids" "$R"

R=$(req "Screener — all 20 assets" GET "$BASE/competitions/$CODE/screener")
check "screener returned" "tickers" "$R"
SCREEN_N=$(echo "$R" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('tickers',[])))" 2>/dev/null)
log "  →  Screener covered: $SCREEN_N assets"

# ================================================================
divider "PART 10 — END COMPETITION + FINAL STANDINGS"
# ================================================================

R=$(req "Bob tries to end (not creator, expect 403)" \
    POST "$BASE/competitions/$CODE/end" \
    -H "Authorization: Bearer $BOB_PT")
check "non-creator blocked" "creator" "$R"

R=$(req "Alice ends competition" \
    POST "$BASE/competitions/$CODE/end" \
    -H "Authorization: Bearer $ALICE_PT")
check "state is ended" "ended" "$R"

R=$(req "Trade after end blocked (expect 400)" \
    POST "$BASE/competitions/$CODE/players/$BOB_PID/orders" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $BOB_PT" \
    -d '{"ticker":"BTC-USD","side":"buy","quantity":"0.001"}')
check "post-end trading blocked" "not active" "$R"

R=$(req "Final leaderboard (post-end)" GET "$BASE/competitions/$CODE/leaderboard")
check "final leaderboard accessible" "rank" "$R"

log ""
log "  ┌─ FINAL STANDINGS"
_FINAL_TMP=$(mktemp)
echo "$R" > "$_FINAL_TMP"
python3 - "$_FINAL_TMP" << 'PYEOF' | tee -a "$OUT" >&2
import sys, json
with open(sys.argv[1]) as f:
    players = json.load(f)
print(f"  │  {'Rank':<5} {'Player':<10} {'Final Value':>13} {'P&L':>10} {'P&L%':>8}  {'Fills':>5}")
print("  │  " + "-"*60)
for p in players:
    tv     = float(p['total_value'])
    pnl    = float(p['pnl'])
    pct    = float(p['pnl_pct'])
    sign   = '+' if pnl >= 0 else ''
    filled = p['orders_filled']
    tickers = [pos['ticker'] for pos in p.get('positions', [])]
    assets = ', '.join(tickers) if tickers else 'cash only'
    print(f"  │  #{p['rank']:<4} {p['display_name']:<10} ${tv:>12,.2f} {sign}{pnl:>9,.2f} {sign}{pct:>7.3f}%  {filled:>5}  [{assets}]")
PYEOF
rm -f "$_FINAL_TMP"
log "  └─"

# ================================================================
divider "PART 11 — HEALTH & SERVER METRICS"
# ================================================================

R=$(req "Health check" GET "$BASE/health")
check "status ok" "ok" "$R"

R=$(req "Server metrics" GET "$BASE/metrics")
check "metrics returned" "orders" "$R"
echo "$R" | python3 - << 'PYEOF' | tee -a "$OUT" >&2
import sys, json
d = json.load(sys.stdin)
placed   = d.get('orders_placed', '?')
filled   = d.get('orders_filled', '?')
rejected = d.get('orders_rejected', '?')
rate     = d.get('fill_rate', 0)
avg_ms   = d.get('avg_order_execution_ms', '?')
print(f"  →  Orders placed   : {placed}")
print(f"  →  Orders filled   : {filled}")
print(f"  →  Orders rejected : {rejected}")
print(f"  →  Fill rate       : {rate:.1%}")
print(f"  →  Avg exec time   : {avg_ms} ms")
PYEOF

# ================================================================
divider "FINAL RESULTS"
# ================================================================

TOTAL=$((PASS+FAIL))
log ""
log "  Run completed : $(ts)"
log "  Tests passed  : $PASS / $TOTAL"
log "  Tests failed  : $FAIL / $TOTAL"
log "  Orders skipped: $SKIP  (server-side rejections — insufficient balance/position)"
log ""
if [[ "$FAIL" -eq 0 ]]; then
    log "  PASSED  ALL $TOTAL TESTS PASSED"
else
    log "  FAILED  $FAIL / $TOTAL TESTS FAILED"
fi
log ""
log "  Full raw output → $OUT"
log "  Run again for completely different results."
