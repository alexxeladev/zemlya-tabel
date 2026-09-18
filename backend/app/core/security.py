from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── Политика паролей (task_stage2_access п.2.3) ──────────────────────────────
# ОДНА на все места, где пароль задаётся: создание доступа, выдача доступа,
# смена своего пароля, сброс админом (генерируется), CLI create-admin /
# reset-password. Вход её НЕ проверяет: пароли, заданные до политики, работают.
PASSWORD_MIN_LENGTH = 8
# bcrypt берёт только первые 72 БАЙТА и остаток молча отбрасывает: пароль
# «72 символа + X» принимался и для «72 символа + Y». Отказываем явно, считая
# байты UTF-8 (кириллица — два байта на букву).
PASSWORD_MAX_BYTES = 72


def password_policy_error(plain: str | None) -> str | None:
    """Причина, по которой пароль не годится, или None."""
    if plain is None or plain == "":
        return "Пароль не может быть пустым"
    if not plain.strip():
        return "Пароль не может состоять из одних пробелов"
    if len(plain) < PASSWORD_MIN_LENGTH:
        return f"Пароль должен быть не короче {PASSWORD_MIN_LENGTH} символов"
    if len(plain.encode("utf-8")) > PASSWORD_MAX_BYTES:
        return (
            f"Пароль слишком длинный: не больше {PASSWORD_MAX_BYTES} байт "
            "(латиница — 72 символа, кириллица — 36)"
        )
    return None


def validate_password(plain: str | None) -> str:
    """Для field_validator схем: ValueError → 422 с причиной."""
    error = password_policy_error(plain)
    if error:
        raise ValueError(error)
    return plain  # type: ignore[return-value]


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(subject: str | int, extra: dict[str, Any] | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(subject), "exp": expire}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except JWTError as exc:
        raise ValueError("Invalid or expired token") from exc
