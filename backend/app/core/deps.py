from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.database import get_db
from app.models.employees import Employee
from app.services.reference_audit import set_audit_actor

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


# Отказ ограниченной сессии (task_stage2_access п.2.2). Текст — признак для
# фронта (`api/client.ts` уводит на смену пароля), менять вместе с ним.
PASSWORD_CHANGE_REQUIRED = "Требуется сменить пароль"


def _authenticate(token: str, db: Session) -> Employee:
    """Токен → действующий сотрудник с доступом. Ограничения сессии здесь НЕ
    проверяются — это делают две зависимости ниже."""
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        employee_id: str | None = payload.get("sub")
        if employee_id is None:
            raise credentials_exc
    except ValueError:
        raise credentials_exc

    emp = db.get(Employee, int(employee_id))
    if emp is None:
        raise credentials_exc
    if emp.email is None or emp.role is None:
        raise credentials_exc
    if not emp.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")
    # Кто правит справочники — журналу изменений (app/services/reference_audit).
    # Ставится ОДИН раз здесь, на сессии запроса: иначе каждый обработчик должен
    # был бы помнить про аудит, и первый же новый забыл бы.
    set_audit_actor(db, emp)
    return emp


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Employee:
    """Полноценная сессия. Через неё идут ВСЕ эндпойнты, кроме своего профиля и
    смены пароля: `require_role` и ролевые зависимости роутеров строятся поверх.

    Пока у пользователя стоит `must_change_password`, сессия ограничена
    (task_stage2_access п.2.2): раньше требование держал только React, и
    выданным при входе токеном можно было работать со всем API.
    """
    emp = _authenticate(token, db)
    if emp.must_change_password:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=PASSWORD_CHANGE_REQUIRED)
    return emp


def get_current_user_allow_password_change(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Employee:
    """Ограниченная сессия: пускает и того, кто обязан сменить пароль. Только
    для `GET /auth/me` и `POST /auth/change-password` — больше никуда."""
    return _authenticate(token, db)


def require_role(*roles: str):
    def dependency(current_user: Employee = Depends(get_current_user)) -> Employee:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return dependency
