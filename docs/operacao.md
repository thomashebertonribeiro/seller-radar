# Operação

> Estado em 29/10/2026: Fase 1 implantada e **publicada em HTTPS** em
> `https://ml.brasilices.tech`, via Nginx Proxy Manager. Documento em construção —
> backup e restauração entram na Fase 6.

## Onde roda

| Item | Valor |
|---|---|
| Diretório | `/srv/seller-radar` |
| Projeto Compose | `seller-radar` |
| `web` | `seller-radar-web-1:8000` na rede `proxy`; também `127.0.0.1:8010` |
| `db` | Postgres 17, sem porta publicada |
| `worker` | sem porta; esqueleto, sem jobs |
| Domínio | `https://ml.brasilices.tech` (TLS Let's Encrypt, HTTP→HTTPS 301) |
| Consumo medido | web 70 MB, worker 9 MB, db 45 MB |

O arquivo `.env` fica em `/srv/seller-radar/.env`, permissão `600`, fora do Git.
Nesta máquina: `APP_ENV=production`, `SESSION_COOKIE_SECURE=true`,
`APP_BASE_URL=https://ml.brasilices.tech`.

O upstream do proxy reverso é resolvido **por nome de container** (`seller-radar-web-1`),
nunca por porta publicada no host. Por isso o serviço `web` declara a rede `proxy` no compose:
conexão manual não sobrevive a `docker compose up -d` (ver `fase-0-5-hardening.md`).

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

Produção: **https://ml.brasilices.tech**

O acesso por `http://127.0.0.1:8010` (túnel) **não serve para login**: com
`SESSION_COOKIE_SECURE=true`, o navegador não envia o cookie em HTTP. Use o domínio.

Para administrar o proxy reverso, o painel fica em `127.0.0.1:81` e exige túnel:

```bash
ssh -L 8081:127.0.0.1:81 radar-vps
# abrir http://localhost:8081
```

## Validação da publicação (29/10/2026)

| Verificação | Resultado |
|---|---|
| Certificado TLS | válido, verificação OK |
| `http://ml.brasilices.tech` | 301 para HTTPS |
| Cookie de sessão | `httponly`, `samesite=lax`, **`secure`** |
| Painel sem sessão | 303 para `/login` |
| `POST /login` sem CSRF | 400 |

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

1. **Backup do Postgres** e **restauração testada** — só vale depois de executada de verdade.
2. **Endurecer o `sshd`** (`PasswordAuthentication no`, `PermitRootLogin no`). Bloqueado pela
   ausência de acesso confirmado ao console do provedor: sem ele, um erro de configuração
   significa perda de acesso.
3. **Firewall do provedor** sem regras. Deliberado até haver forma de rollback fora do SSH.
4. **HSTS** desligado no proxy reverso. Ligar só depois de o certificado se mostrar estável:
   com HSTS ativo, uma falha de TLS vira acesso bloqueado no navegador.
5. **Limite de tentativas de login** passa a ser global, não por cliente: atrás do proxy,
   `request.client.host` é o IP do container do Nginx. Para um usuário único é aceitável, mas
   precisa ser revisto se houver mais de um.

## Princípios

- Nenhum segredo em log ou em documento.
- Toda execução terá `run_id` rastreável do início ao fim (Fase 3).
- Falha da IA não invalida a sincronização; falha da sincronização não derruba o painel.
