from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
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
from app.schemas.auth import ChangePasswordRequest, LoginRequest, TokenResponse
from app.schemas.employee import EmployeeRead
from app.services.finance_masking import employee_for

router = APIRouter()


@router.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    emp: Employee | None = db.query(Employee).filter(Employee.email == payload.email).first()
    if not emp or emp.hashed_password is None or not verify_password(payload.password, emp.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not emp.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive")
    if emp.role is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account has no system access")

    emp.last_login_at = datetime.now(timezone.utc)
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
