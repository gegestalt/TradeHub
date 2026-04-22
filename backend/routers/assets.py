"""Asset registry endpoints: list available tickers for use in competitions."""

from fastapi import APIRouter, Query

from data_adapters.asset_registry import list_assets

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("")
async def get_assets(
    sector: str | None = Query(default=None, description="Filter by sector"),
    asset_type: str | None = Query(
        default=None, description="Filter by type: stock, etf, futures, crypto"
    ),
):
    """Return the curated list of assets available for competition use."""
    assets = list_assets(sector=sector, asset_type=asset_type)
    return {
        "assets": [
            {
                "ticker": a.ticker,
                "name": a.name,
                "sector": a.sector,
                "asset_type": a.asset_type,
            }
            for a in assets
        ],
        "total": len(assets),
    }
