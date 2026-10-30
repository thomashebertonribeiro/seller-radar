"""Comandos administrativos.

Uso:
    python -m seller_radar.cli create-user --email voce@exemplo.com
    python -m seller_radar.cli sync [--full]
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from seller_radar.config import get_settings
from seller_radar.crypto import TokenCipher
from seller_radar.db import SessionLocal
from seller_radar.models import MlCredential, User
from seller_radar.security import hash_password
from seller_radar.sync.engine import reap_stale_runs
from seller_radar.sync.runner import run_sync

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


def sync(full: bool = False) -> int:
    """Sincroniza todas as contas conectadas, uma execução por conta.

    Sem PII na saída: contadores e status apenas, para colar no terminal sem
    expor dados de comprador.
    """
    settings = get_settings()
    cipher = TokenCipher(settings.token_encryption_key)
    mode = "full" if full else "incremental"

    with SessionLocal() as db:
        reaped = reap_stale_runs(db)
        if reaped:
            print(f"{reaped} execução(ões) órfã(s) marcada(s) como falha", file=sys.stderr)

        user_ids = list(
            db.scalars(
                select(User.id)
                .join(MlCredential, MlCredential.user_id == User.id)
                .order_by(User.id)
            )
        )
        if not user_ids:
            print("erro: nenhuma conta do Mercado Livre conectada", file=sys.stderr)
            return 1

        failed = False
        for user_id in user_ids:
            try:
                outcome = run_sync(db, settings, cipher, user_id=user_id, mode=mode)
            except Exception as exc:
                print(
                    f"erro na sincronização da conta {user_id}: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
                failed = True
                continue

            print(
                f"conta {user_id}: status={outcome.status} anúncios={outcome.items} "
                f"pedidos={outcome.orders} dias_de_visita={outcome.visits} "
                f"chamadas={outcome.api_calls} avisos={len(outcome.warnings)}"
            )
            for warning in outcome.warnings[:10]:
                print(f"  aviso: {warning}")

    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seller-radar")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-user", help="cria o usuário inicial")
    create.add_argument("--email", required=True)
    create.add_argument(
        "--password",
        help="senha em texto (evite: fica no histórico do shell; prefira o prompt)",
    )

    sync_cmd = sub.add_parser("sync", help="sincroniza anúncios, pedidos, envios e visitas")
    sync_cmd.add_argument(
        "--full",
        action="store_true",
        help="ignora o checkpoint e rebusca a janela inicial completa",
    )

    args = parser.parse_args(argv)
    if args.command == "create-user":
        return create_user(args.email, args.password)
    if args.command == "sync":
        return sync(full=args.full)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
