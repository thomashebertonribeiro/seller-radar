# Operação

> Estado em 29/10/2026: Fase 1 implantada na VPS, **apenas no loopback**. Nenhum domínio
> publica a aplicação ainda. Documento em construção — backup e restauração entram na Fase 6.

## Onde roda

| Item | Valor |
|---|---|
| Diretório | `/srv/seller-radar` |
| Projeto Compose | `seller-radar` |
| `web` | `127.0.0.1:8010` → container `8000` |
| `db` | Postgres 17, sem porta publicada |
| `worker` | sem porta; esqueleto, sem jobs |
| Consumo medido | web 70 MB, worker 9 MB, db 45 MB |

O arquivo `.env` fica em `/srv/seller-radar/.env`, permissão `600`, fora do Git.

## Primeira execução

```bash
cd /srv/seller-radar
cp .env.example .env          # preencher; nunca versionar
docker compose up -d --build
docker compose exec web alembic upgrade head
docker compose exec web python -m seller_radar.cli create-user --email voce@exemplo.com
```

A senha é pedida no terminal, sem eco e sem passar por argumento de linha de comando.

## Migrações

```bash
docker compose exec web alembic upgrade head      # aplicar
docker compose exec web alembic current           # revisão atual
docker compose exec web alembic downgrade -1      # reverter uma revisão
```

O container **não** aplica migração ao subir. Schema divergente falha visivelmente em vez de
ser "corrigido" por `create_all`.

## Testes

```bash
# suíte padrão, sem banco
docker compose --profile test run --rm test

# fluxo de autenticação contra Postgres real (usuário descartável)
docker compose --profile test run --rm test pytest -q -m integration
```

## Acesso

Enquanto não houver proxy configurado, o acesso é por túnel SSH:

```bash
ssh -L 8010:127.0.0.1:8010 radar-vps
# abrir http://localhost:8010
```

## Atualização de versão

```bash
cd /srv/seller-radar
git pull --ff-only
docker compose build
docker compose up -d
docker compose exec web alembic upgrade head
```

## Diagnóstico

```bash
docker compose ps
docker compose logs --tail 50 web worker
curl -s http://127.0.0.1:8010/healthz
```

## Pendências

1. **Criar o usuário inicial** — ação do operador, com senha digitada no terminal.
2. **Publicar via proxy reverso** com TLS. O candidato é `ml.brasilices.tech`, hoje órfão
   apontando para um container inexistente.
3. **Backup do Postgres** e **restauração testada** — só vale depois de executada de verdade.
4. **Endurecer o `sshd`** (`PasswordAuthentication no`, `PermitRootLogin no`). Bloqueado pela
   ausência de acesso confirmado ao console do provedor: sem ele, um erro de configuração
   significa perda de acesso.
5. **Firewall do provedor** sem regras. Deliberado até haver forma de rollback fora do SSH.

## Princípios

- Nenhum segredo em log ou em documento.
- Toda execução terá `run_id` rastreável do início ao fim (Fase 3).
- Falha da IA não invalida a sincronização; falha da sincronização não derruba o painel.
