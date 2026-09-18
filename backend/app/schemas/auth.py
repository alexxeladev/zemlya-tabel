from pydantic import BaseModel, EmailStr, field_validator

from app.core.security import validate_password


class LoginRequest(BaseModel):
    email: EmailStr
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
