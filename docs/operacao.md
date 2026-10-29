# Operação

> Estado em 29/10/2026: Fase 1 implantada e **publicada em HTTPS** em
> `https://ml.brasilices.tech`, via Nginx Proxy Manager, com **backup diário e
> restauração verificada**. Conexão OAuth com o Mercado Livre implementada (Fase 2).

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

## Backup e restauração

Backup diário do banco, por **systemd timer no host** (usuário `radar`), com o `pg_dump`
executado **dentro do container** do Postgres. Nada é instalado no host e nenhuma
credencial passa pelo shell do host: o container já tem `POSTGRES_USER`/`POSTGRES_DB`
no ambiente.

| Item | Valor |
|---|---|
| Script de backup | `/srv/seller-radar/scripts/backup.sh` |
| Script de verificação | `/srv/seller-radar/scripts/verify-backup.sh` |
| Unidades | `deploy/systemd/seller-radar-backup.{service,timer}` |
| Diretório | `/srv/seller-radar/backups` (dono `radar`, modo `700`) |
| Formato | `pg_dump --format=custom --compress=9` |
| Nome | `radar-AAAAMMDD-HHMMSS.dump` |
| Agenda | todo dia às **06:00 UTC** = 03:00 em America/Sao_Paulo |
| Retenção | 14 dias (`BACKUP_RETENTION_DAYS`) |

Duas garantias do `backup.sh`: (1) `flock` impede duas execuções simultâneas — timer e
execução manual não se atropelam; (2) o arquivo só é publicado depois de passar por
`pg_restore --list`, então um dump truncado nunca entra no diretório.

O horário está escrito em **UTC de propósito**: este host roda em `Etc/UTC` e o
`Timezone=` do systemd 255 não foi honrado aqui — o timer apontava para 03:00 UTC em vez
de 03:00 local. Como o Brasil não tem horário de verão desde 2019, o deslocamento é fixo
e a equivalência é estável.

### Instalação (já aplicada em 29/10/2026)

```bash
install -d -o radar -g radar -m 700 /srv/seller-radar/backups
install -m 644 /srv/seller-radar/deploy/systemd/seller-radar-backup.service /etc/systemd/system/
install -m 644 /srv/seller-radar/deploy/systemd/seller-radar-backup.timer   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now seller-radar-backup.timer
systemctl list-timers seller-radar-backup.timer
```

### Execução e verificação

```bash
# backup manual
sudo -u radar /srv/seller-radar/scripts/backup.sh

# restauração real num banco descartável e comparação de contagens
sudo -u radar /srv/seller-radar/scripts/verify-backup.sh
sudo -u radar /srv/seller-radar/scripts/verify-backup.sh /srv/seller-radar/backups/radar-XXXXXXXX-XXXXXX.dump
```

O `verify-backup.sh` cria `radar_restore_<epoch>`, restaura o dump com `--no-owner
--no-privileges`, compara a contagem de **todas** as tabelas com o banco de origem e
**descarta o banco** ao final, inclusive em caso de erro (`trap`). Divergência em uma
tabela já reprova a verificação com `exit 1`.

### Evidência (29/10/2026)

| Verificação | Resultado |
|---|---|
| Backup manual | `radar-20261029-222716.dump`, 12K, legível por `pg_restore --list` |
| Restauração em banco descartável | `OK: 4 tabelas conferidas, contagens idênticas` |
| Banco descartável removido | sim (nenhum `radar_restore_*` remanescente) |
| Serviço pelo systemd | `Result=success`, `ExecMainStatus=0` |
| Retenção | dump de 20 dias removido; de 3 dias preservado |
| Timer habilitado | `seller-radar-backup.timer` em `timers.target.wants`, próxima execução 06:03 UTC |

O que **não** está coberto: os dumps ficam no mesmo disco do banco. Isso protege contra
erro de operação e corrupção lógica, não contra perda do servidor — cópia externa entra
na Fase 6.

## Conexão com o Mercado Livre

O painel em `/` tem o botão **Conectar conta do Mercado Livre**. O fluxo é:

```
POST /auth/mercadolivre/start      → state + PKCE, redireciona ao Mercado Livre
GET  /auth/mercadolivre/callback   → consome o state, troca o code, grava os tokens
POST /auth/mercadolivre/disconnect → apaga a credencial
```

### Variáveis de ambiente

| Variável | Valor |
|---|---|
| `ML_CLIENT_ID` | App ID do aplicativo no DevCenter |
| `ML_CLIENT_SECRET` | Secret do aplicativo |
| `ML_REDIRECT_URI` | `https://ml.brasilices.tech/auth/mercadolivre/callback` |

As três precisam existir e ser **idênticas** ao cadastro no DevCenter. Sem elas, o painel diz
"aplicativo não configurado" e o botão de conexão não aparece. Os valores nunca vão para o Git,
para log ou para documento — apenas os nomes.

### Registro no DevCenter

O `redirect_uri` enviado na autorização tem de ser **exatamente igual** ao cadastrado, senão o
Mercado Livre recusa com erro antes de chegar ao callback. A lista de Redirect URIs do aplicativo
é aditiva: acrescentar `https://ml.brasilices.tech/auth/mercadolivre/callback` não afeta as URIs
antigas.

Se o aplicativo tiver PKCE habilitado no DevCenter, o `code_challenge` (S256) já é enviado — não
há nada a configurar do lado do código.

### Como saber se está funcionando

1. Abrir `https://ml.brasilices.tech/` autenticado.
2. Clicar em **Conectar conta do Mercado Livre**.
3. Autorizar no site do Mercado Livre.
4. Voltar ao painel com "Conta do Mercado Livre conectada" e o apelido do vendedor.

No banco, a credencial aparece cifrada:

```bash
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "select ml_user_id, ml_nickname, expires_at, needs_reconnect from ml_credentials"
```

Um dump do banco **não** entrega o token: `access_token_enc` e `refresh_token_enc` só fazem
sentido com `TOKEN_ENCRYPTION_KEY`, que fica fora do banco.

### Quando pedir reconexão

`needs_reconnect = true` significa que o provedor recusou o refresh (`invalid_grant`,
`invalid_client`, `invalid_token`). Causas típicas: troca de senha, secret do aplicativo
alterado, revogação do acesso ou 4 meses sem uso. O painel mostra "conectar novamente" e o
sistema **para de tentar** em vez de insistir com o provedor.

### Diagnóstico

| Sintoma | Causa provável |
|---|---|
| Volta com "autorização expirou ou já foi usada" | callback repetido, mais de 10 min no login do ML, ou `state` de outra sessão |
| Volta com "o Mercado Livre recusou a troca do código" | `ML_REDIRECT_URI` diferente do cadastrado, ou `code` já consumido |
| "aplicativo não configurado" | falta uma das três variáveis `ML_*` no `.env` (exige recriar o container) |
| "conta conectada" mas sem apelido | `/users/me` falhou; a conexão em si está válida |

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

1. ~~**Backup do Postgres** e **restauração testada**~~ — feito em 29/10/2026, ver
   [Backup e restauração](#backup-e-restauração). Falta apenas a cópia externa (Fase 6).
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
