export type LobbyState = "lobby" | "active" | "ended";

export interface Lobby {
  code: string;
  name: string;
  playerCount: number;
  maxPlayers: number | null;
  state: LobbyState;
  createdAt: string;
}

export type TradeAsset = "stocks" | "crypto";
export type DataMode = "live" | "offline";
export type OfflineFrameMode = "random" | "specific";
export type OfflineDuration = "1w" | "2w" | "1m" | "3m" | "6m" | "1y";
export type WinCondition =
  | "off"
  | "maximum_final_money"
  | "risk_adjusted_return"
  | "point_system";

export type StockUniverse =
  | "sp500"
  | "nasdaq100"
  | "faang_plus"
  | "dow30"
  | "russell2000"
  | "sp400"
  | "tech_sector"
  | "healthcare_sector"
  | "energy_sector"
  | "financial_sector"
  | "consumer_sector";

export type CryptoUniverse =
  | "btc_only"
  | "btc_eth"
  | "top10"
  | "top50"
  | "defi"
  | "layer2"
  | "meme"
  | "exchange_tokens";

export type PointCriterionId =
  | "final_portfolio_value"
  | "peak_portfolio_value"
  | "best_sharpe_ratio"
  | "first_to_milestone"
  | "sharpest_single_trade"
  | "biggest_single_day_gain"
  | "longest_winning_streak"
  | "most_trades_executed"
  | "best_diversification"
  | "fewest_losing_trades"
  | "largest_absolute_gain"
  | "best_recovery_from_drawdown";

export interface CreateLobbyParams {
  name: string;
  isPrivate: boolean;
  password?: string;
  startingMoney: number;
  gameTimeRate?: number;
  tradeAssets: TradeAsset[];
  stockUniverses?: StockUniverse[];
  cryptoUniverses?: CryptoUniverse[];
  dataMode: DataMode;
  liveEndDateTime?: string;
  offlineFrameMode?: OfflineFrameMode;
  offlineDuration?: OfflineDuration;
  offlineStartDate?: string;
  offlineEndDate?: string;
  winCondition: WinCondition;
  pointCriteria?: { id: PointCriterionId; points: number }[];
}
