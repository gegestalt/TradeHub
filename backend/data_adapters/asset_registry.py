"""Curated list of assets available for competition use with live data via yfinance."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Asset:
    ticker: str
    name: str
    sector: str
    asset_type: str


ASSETS: list[Asset] = [
    # ── S&P 500 / Dow Jones Blue Chips ──────────────────────────────────────
    Asset("AAPL", "Apple", "Technology", "stock"),
    Asset("MSFT", "Microsoft", "Technology", "stock"),
    Asset("GOOGL", "Alphabet (Google)", "Technology", "stock"),
    Asset("AMZN", "Amazon", "Consumer Discretionary", "stock"),
    Asset("NVDA", "NVIDIA", "Technology", "stock"),
    Asset("META", "Meta Platforms", "Technology", "stock"),
    Asset("TSLA", "Tesla", "Consumer Discretionary", "stock"),
    Asset("BRK-B", "Berkshire Hathaway B", "Financials", "stock"),
    Asset("JPM", "JPMorgan Chase", "Financials", "stock"),
    Asset("V", "Visa", "Financials", "stock"),
    Asset("MA", "Mastercard", "Financials", "stock"),
    Asset("UNH", "UnitedHealth Group", "Healthcare", "stock"),
    Asset("JNJ", "Johnson & Johnson", "Healthcare", "stock"),
    Asset("PG", "Procter & Gamble", "Consumer Staples", "stock"),
    Asset("HD", "Home Depot", "Consumer Discretionary", "stock"),
    Asset("IBM", "IBM", "Technology", "stock"),
    Asset("CVX", "Chevron", "Energy", "stock"),
    Asset("XOM", "ExxonMobil", "Energy", "stock"),
    Asset("WMT", "Walmart", "Consumer Staples", "stock"),
    Asset("KO", "Coca-Cola", "Consumer Staples", "stock"),
    Asset("DIS", "Walt Disney", "Communication Services", "stock"),
    Asset("MCD", "McDonald's", "Consumer Discretionary", "stock"),
    Asset("BA", "Boeing", "Industrials", "stock"),
    Asset("CAT", "Caterpillar", "Industrials", "stock"),
    Asset("GS", "Goldman Sachs", "Financials", "stock"),
    Asset("AXP", "American Express", "Financials", "stock"),
    Asset("MMM", "3M", "Industrials", "stock"),
    Asset("NKE", "Nike", "Consumer Discretionary", "stock"),
    Asset("HON", "Honeywell", "Industrials", "stock"),
    Asset("TRV", "Travelers Companies", "Financials", "stock"),
    # ── Cybersecurity ────────────────────────────────────────────────────────
    Asset("CRWD", "CrowdStrike", "Cybersecurity", "stock"),
    Asset("NET", "Cloudflare", "Cybersecurity", "stock"),
    Asset("FTNT", "Fortinet", "Cybersecurity", "stock"),
    Asset("PANW", "Palo Alto Networks", "Cybersecurity", "stock"),
    Asset("ZS", "Zscaler", "Cybersecurity", "stock"),
    Asset("S", "SentinelOne", "Cybersecurity", "stock"),
    # ── Defense & Aerospace ──────────────────────────────────────────────────
    Asset("LMT", "Lockheed Martin", "Defense", "stock"),
    Asset("RTX", "RTX Corporation (Raytheon)", "Defense", "stock"),
    Asset("NOC", "Northrop Grumman", "Defense", "stock"),
    Asset("GD", "General Dynamics", "Defense", "stock"),
    Asset("KTOS", "Kratos Defense", "Defense", "stock"),
    Asset("LDOS", "Leidos Holdings", "Defense", "stock"),
    Asset("SAIC", "Science Applications International", "Defense", "stock"),
    Asset("HII", "Huntington Ingalls Industries", "Defense", "stock"),
    Asset("RHM.DE", "Rheinmetall AG", "Defense", "stock"),
    Asset("BAESY", "BAE Systems", "Defense", "stock"),
    # ── Cloud & Enterprise Software ──────────────────────────────────────────
    Asset("PLTR", "Palantir Technologies", "Technology", "stock"),
    Asset("CRM", "Salesforce", "Technology", "stock"),
    Asset("NOW", "ServiceNow", "Technology", "stock"),
    Asset("SNOW", "Snowflake", "Technology", "stock"),
    Asset("DDOG", "Datadog", "Technology", "stock"),
    Asset("MDB", "MongoDB", "Technology", "stock"),
    Asset("ORCL", "Oracle", "Technology", "stock"),
    Asset("AVGO", "Broadcom", "Technology", "stock"),
    Asset("AMD", "Advanced Micro Devices", "Technology", "stock"),
    Asset("TSM", "Taiwan Semiconductor", "Technology", "stock"),
    # ── Index ETFs ───────────────────────────────────────────────────────────
    Asset("SPY", "SPDR S&P 500 ETF", "ETF", "etf"),
    Asset("QQQ", "Invesco QQQ (Nasdaq-100)", "ETF", "etf"),
    Asset("DIA", "SPDR Dow Jones ETF", "ETF", "etf"),
    Asset("IWM", "iShares Russell 2000 ETF", "ETF", "etf"),
    # ── Commodities (Futures) ────────────────────────────────────────────────
    Asset("CL=F", "WTI Crude Oil Futures", "Commodities", "futures"),
    Asset("NG=F", "Natural Gas Futures", "Commodities", "futures"),
    Asset("GC=F", "Gold Futures", "Commodities", "futures"),
    Asset("SI=F", "Silver Futures", "Commodities", "futures"),
    Asset("BNO", "United States Brent Oil ETF", "Commodities", "etf"),
    Asset("USO", "United States Oil Fund ETF", "Commodities", "etf"),
    # ── Cryptocurrency ───────────────────────────────────────────────────────
    Asset("BTC-USD", "Bitcoin", "Crypto", "crypto"),
    Asset("ETH-USD", "Ethereum", "Crypto", "crypto"),
    Asset("SOL-USD", "Solana", "Crypto", "crypto"),
]

_by_ticker: dict[str, Asset] = {a.ticker: a for a in ASSETS}


def get_asset(ticker: str) -> Asset | None:
    return _by_ticker.get(ticker.upper())


def list_tickers() -> list[str]:
    return [a.ticker for a in ASSETS]


def list_assets(sector: str | None = None, asset_type: str | None = None) -> list[Asset]:
    result = ASSETS
    if sector:
        result = [a for a in result if a.sector.lower() == sector.lower()]
    if asset_type:
        result = [a for a in result if a.asset_type.lower() == asset_type.lower()]
    return result
