from pydantic import BaseModel, Field, field_validator

from app.core.security import validate_password


class LoginRequest(BaseModel):
    # Логин (часть почты до «@») ИЛИ полная почта, регистр не важен
    # (services/accounts). Поле называется email для совместимости с клиентами.
    email: str = Field(min_length=1, max_length=255)
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    must_change_password: bool


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _pwd_policy(cls, v: str) -> str:
        return validate_password(v)
