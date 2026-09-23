from pydantic_settings import BaseSettings, SettingsConfigDict

# Подпись токенов (task_stage2_access п.2.6). С дефолтным «change-me» подпись
# предсказуема: любой, кто видел репозиторий, выписывает себе токен админа.
# Не короче 32 символов — install.sh генерирует 64 hex-символа.
MIN_SECRET_KEY_LENGTH = 32
KNOWN_INSECURE_SECRETS = {"change-me", "changeme", "secret", "secret-key", "dev", "test"}


def secret_key_problem(secret: str | None) -> str | None:
    """Почему с таким SECRET_KEY запускать сервер нельзя, или None."""
    value = (secret or "").strip()
    if not value:
        return "SECRET_KEY не задан"
    if value.lower() in KNOWN_INSECURE_SECRETS:
        return f"SECRET_KEY оставлен значением по умолчанию ({value!r})"
    if len(value) < MIN_SECRET_KEY_LENGTH:
        return f"SECRET_KEY короче {MIN_SECRET_KEY_LENGTH} символов ({len(value)})"
    return None


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+psycopg://tabel:tabel@localhost:5432/tabel"
    SECRET_KEY: str = "change-me"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    # Пробрасывается в create_engine(echo=...) — SQL-лог в консоль (database.py).
    DEBUG: bool = False
    CORS_ORIGINS: str = "http://localhost:5173"

    # Годовой лимит оплачиваемого больничного (дней в календарном году).
    # Первые N дней — 100%, сверх — за свой счёт (задача «Отсутствия», ч.2).
    SICK_LEAVE_LIMIT_DAYS: int = 10

    # Ограничение попыток входа (task_stage2_access п.2.6): столько НЕУДАЧНЫХ
    # попыток на учётку (email) за окно — и вход в неё закрыт до конца окна.
    # Снять раньше может админ («Снять блокировку» / сброс пароля).
    LOGIN_MAX_FAILURES: int = 5
    LOGIN_LOCK_MINUTES: int = 15

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
