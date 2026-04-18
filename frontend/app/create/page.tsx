"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import { createLobby } from "@/lib/api";
import type {
  CreateLobbyParams,
  TradeAsset,
  DataMode,
  OfflineFrameMode,
  OfflineDuration,
  WinCondition,
  StockUniverse,
  CryptoUniverse,
  PointCriterionId,
} from "@/types/lobby";

// ─── Static config ────────────────────────────────────────────────────────────

const STOCK_UNIVERSES: { value: StockUniverse; label: string; desc: string }[] =
  [
    { value: "sp500", label: "S&P 500", desc: "500 large-cap US companies" },
    {
      value: "nasdaq100",
      label: "NASDAQ 100",
      desc: "Top 100 non-financial NASDAQ listings",
    },
    {
      value: "faang_plus",
      label: "FAANG+",
      desc: "AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA",
    },
    { value: "dow30", label: "Dow Jones 30", desc: "30 blue-chip US companies" },
    {
      value: "russell2000",
      label: "Russell 2000",
      desc: "2,000 small-cap US companies",
    },
    {
      value: "sp400",
      label: "S&P 400 Mid-Cap",
      desc: "400 mid-sized US companies",
    },
    {
      value: "tech_sector",
      label: "Tech Sector",
      desc: "Technology stocks (XLK universe)",
    },
    {
      value: "healthcare_sector",
      label: "Healthcare",
      desc: "Healthcare & biotech stocks (XLV universe)",
    },
    {
      value: "energy_sector",
      label: "Energy",
      desc: "Oil, gas & energy stocks (XLE universe)",
    },
    {
      value: "financial_sector",
      label: "Financials",
      desc: "Banks, insurance & fintech (XLF universe)",
    },
    {
      value: "consumer_sector",
      label: "Consumer",
      desc: "Retail & consumer goods (XLY/XLP universe)",
    },
  ];

const CRYPTO_UNIVERSES: {
  value: CryptoUniverse;
  label: string;
  desc: string;
}[] = [
  { value: "btc_only", label: "Bitcoin only", desc: "BTC/USD" },
  { value: "btc_eth", label: "BTC & ETH", desc: "Bitcoin and Ethereum" },
  {
    value: "top10",
    label: "Top 10 coins",
    desc: "10 largest by market cap",
  },
  {
    value: "top50",
    label: "Top 50 coins",
    desc: "50 largest by market cap",
  },
  {
    value: "defi",
    label: "DeFi Tokens",
    desc: "UNI, AAVE, COMP, MKR, CRV and more",
  },
  {
    value: "layer2",
    label: "Layer 2s",
    desc: "MATIC, ARB, OP, IMX and more",
  },
  {
    value: "meme",
    label: "Meme Coins",
    desc: "DOGE, SHIB, PEPE and more",
  },
  {
    value: "exchange_tokens",
    label: "Exchange Tokens",
    desc: "BNB, CRO, OKB and more",
  },
];

const GAME_SPEEDS: { value: string; label: string }[] = [
  { value: "0.25", label: "1 real min  =  15 in-game min" },
  { value: "0.5", label: "1 real min  =  30 in-game min" },
  { value: "1", label: "1 real min  =  1 in-game hour" },
  { value: "4", label: "1 real min  =  4 in-game hours" },
  { value: "8", label: "1 real min  =  8 in-game hours" },
  { value: "24", label: "1 real min  =  1 in-game day" },
  { value: "168", label: "1 real min  =  1 in-game week" },
];

const OFFLINE_DURATIONS: { value: OfflineDuration; label: string }[] = [
  { value: "1w", label: "1 Week" },
  { value: "2w", label: "2 Weeks" },
  { value: "1m", label: "1 Month" },
  { value: "3m", label: "3 Months" },
  { value: "6m", label: "6 Months" },
  { value: "1y", label: "1 Year" },
];

const WIN_CONDITIONS: { value: WinCondition; label: string; desc: string }[] = [
  { value: "off", label: "Off", desc: "No winner — play for fun." },
  {
    value: "maximum_final_money",
    label: "Maximum Final Money",
    desc: "Highest portfolio value at the end wins.",
  },
  {
    value: "risk_adjusted_return",
    label: "Risk Adjusted Return",
    desc: "Best return relative to risk taken (Sharpe ratio).",
  },
  {
    value: "point_system",
    label: "Point System",
    desc: "Earn points across multiple performance categories.",
  },
];

interface PointCriterion {
  id: PointCriterionId;
  label: string;
  desc: string;
  defaultPoints: number;
}

const POINT_CRITERIA: PointCriterion[] = [
  {
    id: "final_portfolio_value",
    label: "Final Portfolio Value",
    desc: "Highest total portfolio value at end of game.",
    defaultPoints: 10,
  },
  {
    id: "peak_portfolio_value",
    label: "Peak Portfolio Value",
    desc: "Highest portfolio value reached at any point during the game.",
    defaultPoints: 7,
  },
  {
    id: "best_sharpe_ratio",
    label: "Best Risk-Adjusted Return",
    desc: "Highest Sharpe ratio across all closed trades.",
    defaultPoints: 8,
  },
  {
    id: "first_to_milestone",
    label: "First to +20% Return",
    desc: "First player to achieve a +20% gain on their starting balance.",
    defaultPoints: 8,
  },
  {
    id: "sharpest_single_trade",
    label: "Sharpest Single Trade",
    desc: "Largest percentage gain on a single closed position.",
    defaultPoints: 6,
  },
  {
    id: "biggest_single_day_gain",
    label: "Biggest Single-Day Gain",
    desc: "Largest portfolio value increase within one trading day.",
    defaultPoints: 5,
  },
  {
    id: "longest_winning_streak",
    label: "Longest Winning Streak",
    desc: "Most consecutive profitable closed trades.",
    defaultPoints: 5,
  },
  {
    id: "most_trades_executed",
    label: "Most Active Trader",
    desc: "Player who placed the highest number of orders.",
    defaultPoints: 3,
  },
  {
    id: "best_diversification",
    label: "Best Diversification",
    desc: "Traded the most distinct asset categories.",
    defaultPoints: 3,
  },
  {
    id: "fewest_losing_trades",
    label: "Highest Win Rate",
    desc: "Lowest percentage of losing trades (minimum 5 trades required).",
    defaultPoints: 6,
  },
  {
    id: "largest_absolute_gain",
    label: "Largest Absolute Gain",
    desc: "Biggest dollar profit on a single closed position.",
    defaultPoints: 5,
  },
  {
    id: "best_recovery_from_drawdown",
    label: "Best Comeback",
    desc: "Biggest portfolio recovery after hitting a drawdown low.",
    defaultPoints: 4,
  },
];

const DEFAULT_CRITERIA_POINTS: Record<PointCriterionId, number> = {
  final_portfolio_value: 10,
  peak_portfolio_value: 7,
  best_sharpe_ratio: 8,
  first_to_milestone: 8,
  sharpest_single_trade: 6,
  biggest_single_day_gain: 5,
  longest_winning_streak: 5,
  most_trades_executed: 3,
  best_diversification: 3,
  fewest_losing_trades: 6,
  largest_absolute_gain: 5,
  best_recovery_from_drawdown: 4,
};

const DIVIDER = <div className="border-t border-zinc-800" aria-hidden />;

// ─── Component ────────────────────────────────────────────────────────────────

export default function CreateLobbyPage() {
  const router = useRouter();

  // Basics
  const [name, setName] = useState("");
  const [isPrivate, setIsPrivate] = useState(false);
  const [password, setPassword] = useState("");
  const [startingMoney, setStartingMoney] = useState("10000");

  // Trade options
  const [tradeAssets, setTradeAssets] = useState<TradeAsset[]>([
    "stocks",
    "crypto",
  ]);
  const [stockUniverses, setStockUniverses] = useState<StockUniverse[]>([
    "sp500",
  ]);
  const [cryptoUniverses, setCryptoUniverses] = useState<CryptoUniverse[]>([
    "top10",
  ]);

  // Data source
  const [dataMode, setDataMode] = useState<DataMode>("live");
  const [liveEndDateTime, setLiveEndDateTime] = useState("");
  const [offlineFrameMode, setOfflineFrameMode] =
    useState<OfflineFrameMode>("random");
  const [offlineDuration, setOfflineDuration] =
    useState<OfflineDuration>("1m");
  const [offlineStartDate, setOfflineStartDate] = useState("");
  const [offlineEndDate, setOfflineEndDate] = useState("");
  const [gameTimeRate, setGameTimeRate] = useState("1");

  // Win condition
  const [winCondition, setWinCondition] = useState<WinCondition>("off");
  const [selectedCriteria, setSelectedCriteria] = useState<PointCriterionId[]>(
    []
  );
  const [criteriaPoints, setCriteriaPoints] = useState<
    Record<PointCriterionId, number>
  >(DEFAULT_CRITERIA_POINTS);

  // Form state
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ─── Handlers ───────────────────────────────────────────────────────────────

  function toggleAsset(asset: TradeAsset) {
    setTradeAssets((prev) =>
      prev.includes(asset) ? prev.filter((a) => a !== asset) : [...prev, asset]
    );
  }

  function toggleStockUniverse(u: StockUniverse) {
    setStockUniverses((prev) =>
      prev.includes(u) ? prev.filter((x) => x !== u) : [...prev, u]
    );
  }

  function toggleCryptoUniverse(u: CryptoUniverse) {
    setCryptoUniverses((prev) =>
      prev.includes(u) ? prev.filter((x) => x !== u) : [...prev, u]
    );
  }

  function toggleCriterion(id: PointCriterionId) {
    setSelectedCriteria((prev) =>
      prev.includes(id) ? prev.filter((c) => c !== id) : [...prev, id]
    );
  }

  function updateCriterionPoints(id: PointCriterionId, raw: string) {
    const n = Number(raw);
    if (Number.isFinite(n) && n >= 0) {
      setCriteriaPoints((prev) => ({ ...prev, [id]: n }));
    }
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);

    if (!name.trim()) {
      setError("Lobby name is required.");
      return;
    }
    if (isPrivate && !password.trim()) {
      setError("A password is required for private lobbies.");
      return;
    }
    const money = Number(startingMoney);
    if (!Number.isFinite(money) || money <= 0) {
      setError("Starting money must be a positive number.");
      return;
    }
    if (tradeAssets.length === 0) {
      setError("Select at least one trade asset.");
      return;
    }
    if (tradeAssets.includes("stocks") && stockUniverses.length === 0) {
      setError("Select at least one stock universe.");
      return;
    }
    if (tradeAssets.includes("crypto") && cryptoUniverses.length === 0) {
      setError("Select at least one crypto universe.");
      return;
    }
    if (dataMode === "live" && !liveEndDateTime) {
      setError("Select an end date and time for Live mode.");
      return;
    }
    if (
      dataMode === "offline" &&
      offlineFrameMode === "specific" &&
      (!offlineStartDate || !offlineEndDate)
    ) {
      setError("Select a start and end date for the specific frame.");
      return;
    }
    if (winCondition === "point_system" && selectedCriteria.length === 0) {
      setError("Select at least one point criterion.");
      return;
    }

    const params: CreateLobbyParams = {
      name: name.trim(),
      isPrivate,
      ...(isPrivate ? { password: password.trim() } : {}),
      startingMoney: money,
      tradeAssets,
      ...(tradeAssets.includes("stocks") ? { stockUniverses } : {}),
      ...(tradeAssets.includes("crypto") ? { cryptoUniverses } : {}),
      dataMode,
      ...(dataMode === "live" ? { liveEndDateTime } : {}),
      ...(dataMode === "offline"
        ? {
            gameTimeRate: Number(gameTimeRate),
            offlineFrameMode,
            ...(offlineFrameMode === "random" ? { offlineDuration } : {}),
            ...(offlineFrameMode === "specific"
              ? { offlineStartDate, offlineEndDate }
              : {}),
          }
        : {}),
      winCondition,
      ...(winCondition === "point_system"
        ? {
            pointCriteria: selectedCriteria.map((id) => ({
              id,
              points: criteriaPoints[id],
            })),
          }
        : {}),
    };

    setSubmitting(true);
    try {
      const { code } = await createLobby(params);
      router.push(`/${code}`);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to create lobby."
      );
      setSubmitting(false);
    }
  }

  // ─── Render ─────────────────────────────────────────────────────────────────

  return (
    <main className="max-w-2xl mx-auto px-4 py-12">
      <header className="mb-8 flex items-start justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Create Lobby</h1>
          <p className="text-zinc-400 mt-1">
            Set up your competition and share the code with friends.
          </p>
        </div>
        <button
          type="button"
          onClick={() => router.back()}
          className="text-zinc-400 hover:text-zinc-200 text-sm transition-colors mt-1 shrink-0"
        >
          ← Back
        </button>
      </header>

      <form onSubmit={handleSubmit} className="flex flex-col gap-6">
        {/* ── Lobby Basics ── */}
        <section className="flex flex-col gap-4">
          <h2 className="text-xs text-zinc-400 uppercase tracking-wider">
            Lobby Basics
          </h2>

          <Input
            label="Lobby Name"
            placeholder="e.g. Friday Trading Cup"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoComplete="off"
          />

          <div className="flex flex-col gap-3">
            <label className="flex items-center gap-3 cursor-pointer group w-fit">
              <input
                type="checkbox"
                checked={isPrivate}
                onChange={(e) => setIsPrivate(e.target.checked)}
                className="accent-green-500 w-4 h-4 cursor-pointer"
              />
              <span className="text-sm text-zinc-200 group-hover:text-white transition-colors">
                Private lobby
              </span>
            </label>
            {isPrivate && (
              <Input
                label="Password"
                type="password"
                placeholder="Players will need this to join"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
              />
            )}
          </div>

          <Input
            label="Starting Money ($)"
            type="number"
            min={1}
            step={100}
            value={startingMoney}
            onChange={(e) => setStartingMoney(e.target.value)}
            className="font-mono"
          />
        </section>

        {DIVIDER}

        {/* ── Trade Options ── */}
        <section className="flex flex-col gap-4">
          <h2 className="text-xs text-zinc-400 uppercase tracking-wider">
            Trade Options
          </h2>

          {/* Stocks */}
          <div className="flex flex-col gap-3">
            <label className="flex items-center gap-3 cursor-pointer group w-fit">
              <input
                type="checkbox"
                checked={tradeAssets.includes("stocks")}
                onChange={() => toggleAsset("stocks")}
                className="accent-green-500 w-4 h-4 cursor-pointer"
              />
              <span className="text-sm font-medium text-zinc-200 group-hover:text-white transition-colors">
                Stocks
              </span>
            </label>
            {tradeAssets.includes("stocks") && (
              <div className="ml-7 grid grid-cols-2 gap-2">
                {STOCK_UNIVERSES.map((u) => (
                  <label
                    key={u.value}
                    className={`flex items-start gap-2.5 cursor-pointer rounded-lg border p-2.5 transition-colors ${
                      stockUniverses.includes(u.value)
                        ? "border-green-500/50 bg-green-500/5"
                        : "border-zinc-800 hover:border-zinc-600"
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={stockUniverses.includes(u.value)}
                      onChange={() => toggleStockUniverse(u.value)}
                      className="accent-green-500 w-4 h-4 cursor-pointer mt-0.5 shrink-0"
                    />
                    <div className="min-w-0">
                      <p className="text-sm text-zinc-200 font-medium leading-tight">
                        {u.label}
                      </p>
                      <p className="text-xs text-zinc-500 mt-0.5">{u.desc}</p>
                    </div>
                  </label>
                ))}
              </div>
            )}
          </div>

          {/* Crypto */}
          <div className="flex flex-col gap-3">
            <label className="flex items-center gap-3 cursor-pointer group w-fit">
              <input
                type="checkbox"
                checked={tradeAssets.includes("crypto")}
                onChange={() => toggleAsset("crypto")}
                className="accent-green-500 w-4 h-4 cursor-pointer"
              />
              <span className="text-sm font-medium text-zinc-200 group-hover:text-white transition-colors">
                Crypto
              </span>
            </label>
            {tradeAssets.includes("crypto") && (
              <div className="ml-7 grid grid-cols-2 gap-2">
                {CRYPTO_UNIVERSES.map((u) => (
                  <label
                    key={u.value}
                    className={`flex items-start gap-2.5 cursor-pointer rounded-lg border p-2.5 transition-colors ${
                      cryptoUniverses.includes(u.value)
                        ? "border-green-500/50 bg-green-500/5"
                        : "border-zinc-800 hover:border-zinc-600"
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={cryptoUniverses.includes(u.value)}
                      onChange={() => toggleCryptoUniverse(u.value)}
                      className="accent-green-500 w-4 h-4 cursor-pointer mt-0.5 shrink-0"
                    />
                    <div className="min-w-0">
                      <p className="text-sm text-zinc-200 font-medium leading-tight">
                        {u.label}
                      </p>
                      <p className="text-xs text-zinc-500 mt-0.5">{u.desc}</p>
                    </div>
                  </label>
                ))}
              </div>
            )}
          </div>
        </section>

        {DIVIDER}

        {/* ── Data Source ── */}
        <section className="flex flex-col gap-4">
          <h2 className="text-xs text-zinc-400 uppercase tracking-wider">
            Data Source
          </h2>

          <div className="flex rounded-lg border border-zinc-700 overflow-hidden">
            {(["live", "offline"] as DataMode[]).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => setDataMode(mode)}
                className={`flex-1 py-2.5 text-sm transition-colors ${
                  dataMode === mode
                    ? "bg-green-500 text-black font-semibold"
                    : "bg-zinc-800 text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {mode.charAt(0).toUpperCase() + mode.slice(1)}
              </button>
            ))}
          </div>

          {dataMode === "live" && (
            <div className="flex flex-col gap-1.5">
              <label className="text-xs text-zinc-400 uppercase tracking-wider">
                End Date & Time
              </label>
              <input
                type="datetime-local"
                value={liveEndDateTime}
                onChange={(e) => setLiveEndDateTime(e.target.value)}
                className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-4 py-2.5 text-zinc-100 focus:outline-none focus:border-green-500 focus:ring-1 focus:ring-green-500/30 transition-colors"
              />
            </div>
          )}

          {dataMode === "offline" && (
            <div className="flex flex-col gap-4">
              <div className="flex flex-col gap-1.5">
                <label className="text-xs text-zinc-400 uppercase tracking-wider">
                  Game Speed
                </label>
                <select
                  value={gameTimeRate}
                  onChange={(e) => setGameTimeRate(e.target.value)}
                  className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-4 py-2.5 text-zinc-100 focus:outline-none focus:border-green-500 focus:ring-1 focus:ring-green-500/30 transition-colors cursor-pointer appearance-none font-mono text-sm"
                >
                  {GAME_SPEEDS.map((s) => (
                    <option key={s.value} value={s.value}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </div>

              <div className="flex flex-col gap-2">
                <p className="text-xs text-zinc-400 uppercase tracking-wider">
                  Frame Selection
                </p>
                {(["random", "specific"] as OfflineFrameMode[]).map((mode) => (
                  <label
                    key={mode}
                    className="flex items-start gap-3 cursor-pointer group"
                  >
                    <input
                      type="radio"
                      name="offlineFrameMode"
                      value={mode}
                      checked={offlineFrameMode === mode}
                      onChange={() => setOfflineFrameMode(mode)}
                      className="accent-green-500 w-4 h-4 cursor-pointer mt-0.5 shrink-0"
                    />
                    <div>
                      <span className="text-sm text-zinc-200 group-hover:text-white transition-colors">
                        {mode === "random" ? "Random frame" : "Specific frame"}
                      </span>
                      <p className="text-xs text-zinc-500 mt-0.5">
                        {mode === "random"
                          ? "A random window of your chosen duration is picked from historical data."
                          : "Players trade within an exact date range you specify."}
                      </p>
                    </div>
                  </label>
                ))}
              </div>

              {offlineFrameMode === "random" && (
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs text-zinc-400 uppercase tracking-wider">
                    Duration
                  </label>
                  <select
                    value={offlineDuration}
                    onChange={(e) =>
                      setOfflineDuration(e.target.value as OfflineDuration)
                    }
                    className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-4 py-2.5 text-zinc-100 focus:outline-none focus:border-green-500 focus:ring-1 focus:ring-green-500/30 transition-colors cursor-pointer appearance-none"
                  >
                    {OFFLINE_DURATIONS.map((d) => (
                      <option key={d.value} value={d.value}>
                        {d.label}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              {offlineFrameMode === "specific" && (
                <div className="flex gap-4">
                  <div className="flex-1">
                    <Input
                      label="Start Date"
                      type="date"
                      value={offlineStartDate}
                      onChange={(e) => setOfflineStartDate(e.target.value)}
                    />
                  </div>
                  <div className="flex-1">
                    <Input
                      label="End Date"
                      type="date"
                      value={offlineEndDate}
                      onChange={(e) => setOfflineEndDate(e.target.value)}
                    />
                  </div>
                </div>
              )}
            </div>
          )}
        </section>

        {DIVIDER}

        {/* ── Win Condition ── */}
        <section className="flex flex-col gap-3">
          <h2 className="text-xs text-zinc-400 uppercase tracking-wider">
            Win Condition
          </h2>

          <div className="flex flex-col gap-2">
            {WIN_CONDITIONS.map(({ value, label, desc }) => (
              <label
                key={value}
                className={`flex items-start gap-3 cursor-pointer rounded-lg border p-3 transition-colors ${
                  winCondition === value
                    ? "border-green-500 bg-green-500/5"
                    : "border-zinc-800 hover:border-zinc-600"
                }`}
              >
                <input
                  type="radio"
                  name="winCondition"
                  value={value}
                  checked={winCondition === value}
                  onChange={() => setWinCondition(value)}
                  className="accent-green-500 w-4 h-4 cursor-pointer mt-0.5 shrink-0"
                />
                <div>
                  <p className="text-sm font-medium text-zinc-200">{label}</p>
                  <p className="text-xs text-zinc-500 mt-0.5">{desc}</p>
                </div>
              </label>
            ))}
          </div>

          {winCondition === "point_system" && (
            <div className="flex flex-col gap-3 mt-1">
              <p className="text-xs text-zinc-400 uppercase tracking-wider">
                Point Criteria
                <span className="normal-case ml-2 text-zinc-600">
                  — select which achievements award points
                </span>
              </p>
              <div className="flex flex-col gap-2">
                {POINT_CRITERIA.map((criterion) => {
                  const active = selectedCriteria.includes(criterion.id);
                  return (
                    <div
                      key={criterion.id}
                      className={`flex items-start gap-3 rounded-lg border p-3 transition-colors ${
                        active
                          ? "border-green-500/40 bg-green-500/5"
                          : "border-zinc-800 hover:border-zinc-700"
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={active}
                        onChange={() => toggleCriterion(criterion.id)}
                        className="accent-green-500 w-4 h-4 cursor-pointer mt-0.5 shrink-0"
                      />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-zinc-200">
                          {criterion.label}
                        </p>
                        <p className="text-xs text-zinc-500 mt-0.5">
                          {criterion.desc}
                        </p>
                      </div>
                      {active && (
                        <div className="flex items-center gap-1.5 shrink-0">
                          <input
                            type="number"
                            min={1}
                            value={criteriaPoints[criterion.id]}
                            onChange={(e) =>
                              updateCriterionPoints(criterion.id, e.target.value)
                            }
                            className="w-20 bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-zinc-100 text-sm font-mono text-right focus:outline-none focus:border-green-500 transition-colors"
                          />
                          <span className="text-xs text-zinc-500 w-5">pts</span>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </section>

        {error && (
          <p
            className="text-red-400 text-sm bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3"
            role="alert"
          >
            {error}
          </p>
        )}

        <Button type="submit" variant="primary" disabled={submitting}>
          {submitting ? "Creating…" : "Create Lobby"}
        </Button>
      </form>
    </main>
  );
}
