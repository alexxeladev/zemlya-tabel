"""CLI utilities for zemlya-tabel.

Usage:
    python -m app.cli create-admin --email admin@example.com --password changeme --full-name "Admin"
    python -m app.cli reset-password --email admin@example.com --new-password newpass
"""
import argparse
import sys


def create_admin(email: str, password: str, full_name: str) -> None:
    from app.core.security import hash_password
    from app.database import SessionLocal
    from app.models.employees import Employee

    db = SessionLocal()
    try:
        existing = db.query(Employee).filter(Employee.is_system_admin.is_(True)).first()
        if existing:
            print(
                f"Error: System admin already exists (email: {existing.email}). "
                "Use reset-password instead.",
                file=sys.stderr,
            )
            sys.exit(1)

        if db.query(Employee).filter(Employee.email == email).first():
            print(f"Error: employee with email '{email}' already exists.", file=sys.stderr)
            sys.exit(1)

        emp = Employee(
            full_name=full_name,
            email=email,
            hashed_password=hash_password(password),
            role="admin",
            is_active=True,
            must_change_password=True,
            is_system_admin=True,
        )
        db.add(emp)
        db.commit()
        print(f"System admin '{email}' created. must_change_password=True")
    finally:
        db.close()


def reset_password(email: str, new_password: str) -> None:
    from app.core.security import hash_password
    from app.database import SessionLocal
    from app.models.employees import Employee

    db = SessionLocal()
    try:
        emp = db.query(Employee).filter(Employee.email == email).first()
        if not emp:
            print(f"Error: no employee with email '{email}' found.", file=sys.stderr)
            sys.exit(1)

        emp.hashed_password = hash_password(new_password)
        emp.must_change_password = True
        db.commit()
        print(f"Password reset for '{email}'. must_change_password=True")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="zemlya-tabel CLI")
    subparsers = parser.add_subparsers(dest="command")

    cmd = subparsers.add_parser("create-admin", help="Create initial system admin employee")
    cmd.add_argument("--email", required=True)
    cmd.add_argument("--password", required=True)
    cmd.add_argument("--full-name", required=True, dest="full_name")

    cmd2 = subparsers.add_parser("reset-password", help="Reset password for an employee")
    cmd2.add_argument("--email", required=True)
    cmd2.add_argument("--new-password", required=True, dest="new_password")

    args = parser.parse_args()
    if args.command == "create-admin":
        create_admin(args.email, args.password, args.full_name)
    elif args.command == "reset-password":
        reset_password(args.email, args.new_password)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
