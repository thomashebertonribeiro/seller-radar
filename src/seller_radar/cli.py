"""Comandos administrativos.

Uso:
    python -m seller_radar.cli create-user --email voce@exemplo.com
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from seller_radar.db import SessionLocal
from seller_radar.models import User
from seller_radar.security import hash_password

MIN_PASSWORD_LENGTH = 12


def create_user(email: str, password: str | None = None) -> int:
    email = email.strip().lower()
    if not email or "@" not in email:
        print("erro: e-mail inválido", file=sys.stderr)
        return 2

    if password is None:
        password = getpass.getpass("Senha: ")
        confirm = getpass.getpass("Confirme a senha: ")
        if password != confirm:
            print("erro: as senhas não coincidem", file=sys.stderr)
            return 2

    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"erro: senha precisa de ao menos {MIN_PASSWORD_LENGTH} caracteres", file=sys.stderr)
        return 2

    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == email)) is not None:
            print(f"erro: usuário {email} já existe", file=sys.stderr)
            return 1
        db.add(User(email=email, password_hash=hash_password(password)))
        db.commit()

    print(f"usuário {email} criado")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seller-radar")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-user", help="cria o usuário inicial")
    create.add_argument("--email", required=True)
    create.add_argument(
        "--password",
        help="senha em texto (evite: fica no histórico do shell; prefira o prompt)",
    )

    args = parser.parse_args(argv)
    if args.command == "create-user":
        return create_user(args.email, args.password)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
