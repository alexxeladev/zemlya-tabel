"""CLI utilities for zemlya-tabel.

Usage:
    python -m app.cli create-admin --email admin@example.com --password changeme --full-name "Admin"
"""
import argparse
import sys


def create_admin(email: str, password: str, full_name: str) -> None:
    from app.core.security import hash_password
    from app.database import SessionLocal
    from app.models.users import User, UserRole

    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == email).first():
            print(f"Error: user with email '{email}' already exists.", file=sys.stderr)
            sys.exit(1)

        user = User(
            email=email,
            full_name=full_name,
            hashed_password=hash_password(password),
            role=UserRole.admin,
            is_active=True,
            must_change_password=True,
        )
        db.add(user)
        db.commit()
        print(f"Admin '{email}' created. must_change_password=True")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="zemlya-tabel CLI")
    subparsers = parser.add_subparsers(dest="command")

    cmd = subparsers.add_parser("create-admin", help="Create initial admin user")
    cmd.add_argument("--email", required=True)
    cmd.add_argument("--password", required=True)
    cmd.add_argument("--full-name", required=True, dest="full_name")

    args = parser.parse_args()
    if args.command == "create-admin":
        create_admin(args.email, args.password, args.full_name)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
