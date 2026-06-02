from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.database import get_db
from app.models.employees import Employee
from app.schemas.auth import ChangePasswordRequest, LoginRequest, TokenResponse
from app.schemas.employee import EmployeeRead

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

    token = create_access_token(subject=emp.id)
    return TokenResponse(
        access_token=token,
        must_change_password=emp.must_change_password,
    )


@router.post("/auth/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest,
    current_emp: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_emp.hashed_password is None or not verify_password(payload.current_password, current_emp.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Wrong current password")
    current_emp.hashed_password = hash_password(payload.new_password)
    current_emp.must_change_password = False
    db.commit()


@router.get("/auth/me", response_model=EmployeeRead)
def me(current_emp: Employee = Depends(get_current_user)):
    return current_emp
