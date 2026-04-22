import secrets

from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User


async def register_user(db: AsyncSession, display_name: str) -> tuple[User, str]:
    token = secrets.token_urlsafe(32)
    user = User(display_name=display_name, token=token)
    db.add(user)
    await db.flush()
    return user, token
