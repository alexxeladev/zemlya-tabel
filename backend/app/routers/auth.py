from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_allow_password_change
from app.core.security import (
    TOKEN_VERSION_CLAIM,
    create_access_token,
    hash_password,
    revoke_sessions,
    verify_password,
)
from app.database import get_db
from app.models.employees import Employee
from app.models.login_failures import (
    REASON_INACTIVE,
    REASON_LOCKED,
    REASON_NO_ACCESS,
    REASON_UNKNOWN_EMAIL,
    REASON_WRONG_PASSWORD,
)
from app.schemas.auth import ChangePasswordRequest, LoginRequest, TokenResponse
from app.schemas.employee import EmployeeRead
from app.services.finance_masking import employee_for
from app.services.login_guard import (
    client_ip,
    login_locked_until,
    record_failure,
    serialize_attempts,
)

router = APIRouter()


@router.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """Вход. Неудачи пишутся в журнал `login_failures`, после
    `LOGIN_MAX_FAILURES` неудач за окно учётка закрыта до его конца — 429
    (task_stage2_access п.2.6). Блокировка проверяется ДО пароля: во время неё
    даже верный пароль не пускает, иначе перебор продолжался бы."""
    ip = client_ip(request)
    serialize_attempts(db, payload.email)
    emp: Employee | None = db.query(Employee).filter(Employee.email == payload.email).first()

    def reject(reason: str, status_code: int, detail: str, headers: dict | None = None):
        record_failure(db, payload.email, ip, reason, emp)
        db.commit()
        raise HTTPException(status_code=status_code, detail=detail, headers=headers)

    locked_until = login_locked_until(db, payload.email, emp)
    if locked_until is not None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        retry = max(1, int((locked_until - now).total_seconds()))
        minutes = (retry + 59) // 60
        reject(
            REASON_LOCKED, status.HTTP_429_TOO_MANY_REQUESTS,
            f"Слишком много неудачных попыток входа. Вход в учётную запись закрыт "
            f"ещё на {minutes} мин. Снять блокировку раньше может администратор.",
            {"Retry-After": str(retry)},
        )
    if not emp:
        reject(REASON_UNKNOWN_EMAIL, status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    if emp.hashed_password is None or not verify_password(payload.password, emp.hashed_password):
        reject(REASON_WRONG_PASSWORD, status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    if not emp.is_active:
        reject(REASON_INACTIVE, status.HTTP_403_FORBIDDEN, "Account is inactive")
    if emp.role is None:
        reject(REASON_NO_ACCESS, status.HTTP_401_UNAUTHORIZED, "Account has no system access")

    # Naive UTC, как и колонка: aware-время в колонку без пояса Postgres
    # переводит по TimeZone сессии, и точка сброса счётчика неудач уехала бы.
    emp.last_login_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()

    token = create_access_token(subject=emp.id, extra={TOKEN_VERSION_CLAIM: emp.token_version})
    return TokenResponse(
        access_token=token,
        must_change_password=emp.must_change_password,
    )


@router.post("/auth/change-password", response_model=TokenResponse)
def change_password(
    payload: ChangePasswordRequest,
    current_emp: Employee = Depends(get_current_user_allow_password_change),
    db: Session = Depends(get_db),
):
    """Смена своего пароля ОТЗЫВАЕТ все выданные токены, включая тот, которым
    пришёл запрос (task_stage2_access п.2.4), — поэтому в ответе новый токен:
    иначе человек, сменивший пароль, тут же вылетал бы на вход."""
    if current_emp.hashed_password is None or not verify_password(payload.current_password, current_emp.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Wrong current password")
    current_emp.hashed_password = hash_password(payload.new_password)
    current_emp.must_change_password = False
    revoke_sessions(current_emp)
    db.commit()
    token = create_access_token(
        subject=current_emp.id, extra={TOKEN_VERSION_CLAIM: current_emp.token_version}
    )
    return TokenResponse(access_token=token, must_change_password=False)


@router.get("/auth/me", response_model=EmployeeRead)
def me(current_emp: Employee = Depends(get_current_user_allow_password_change)):
    # Своя карточка — тоже карточка. Табельщику она отдаётся без денег вовсе;
    # сотруднику — со СВОИМ окладом (это его данные), но без бюджета отдела:
    # фонд ночных смен приходит вложенным в `department` (task_stage1 п.1.3).
    return employee_for(current_emp, EmployeeRead.model_validate(current_emp))
