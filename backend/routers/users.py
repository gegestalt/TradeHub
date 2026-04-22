from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from schemas.user import UserOut, UserRegister
from services.user import register_user

router = APIRouter(prefix="/users", tags=["users"])


@router.post("/register", response_model=UserOut, status_code=201)
async def register(data: UserRegister, db: AsyncSession = Depends(get_db)):
    user, token = await register_user(db, data.display_name)
    return UserOut(user_id=user.id, token=token, display_name=user.display_name)
