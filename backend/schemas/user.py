from pydantic import BaseModel, Field


class UserRegister(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=100)


class UserOut(BaseModel):
    user_id: str
    token: str
    display_name: str
