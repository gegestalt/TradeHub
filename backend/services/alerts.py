"""Price alert management and evaluation."""

from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.alert import PriceAlert
from models.enums import AlertCondition
from models.player import Player
from schemas.alert import AlertCreate


async def create_alert(db: AsyncSession, player: Player, data: AlertCreate) -> PriceAlert:
    alert = PriceAlert(
        player_id=player.id,
        ticker=data.ticker,
        condition=data.condition,
        price=data.price,
    )
    db.add(alert)
    await db.flush()
    return alert


async def list_alerts(db: AsyncSession, player: Player) -> list[PriceAlert]:
    result = await db.execute(
        select(PriceAlert)
        .where(PriceAlert.player_id == player.id)
        .order_by(PriceAlert.created_at.desc())
    )
    return list(result.scalars().all())


async def delete_alert(db: AsyncSession, player: Player, alert_id: str) -> None:
    result = await db.execute(
        select(PriceAlert).where(
            PriceAlert.id == alert_id, PriceAlert.player_id == player.id
        )
    )
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    await db.delete(alert)


def _is_triggered(condition: AlertCondition, threshold: Decimal, current: Decimal) -> bool:
    if condition == AlertCondition.above:
        return current >= threshold
    if condition == AlertCondition.below:
        return current <= threshold
    # crosses: always trigger on first evaluation when price is near threshold
    return abs(current - threshold) / threshold < Decimal("0.001")


async def check_and_fire_alerts(
    db: AsyncSession,
    ticker: str,
    current_price: Decimal,
) -> list[PriceAlert]:
    """Evaluate all untriggered alerts for `ticker`. Returns fired alerts."""
    result = await db.execute(
        select(PriceAlert).where(
            PriceAlert.ticker == ticker,
            PriceAlert.triggered.is_(False),
        )
    )
    alerts = result.scalars().all()

    fired: list[PriceAlert] = []
    for alert in alerts:
        if _is_triggered(alert.condition, alert.price, current_price):
            alert.triggered = True
            alert.triggered_at = datetime.utcnow()
            fired.append(alert)
    return fired
