#!/usr/bin/env bash
# Verificação de backup: restaura o dump num banco descartável e compara
# a contagem de linhas de cada tabela com o banco de origem.
#
# Uso:
#   scripts/verify-backup.sh                    # usa o dump mais recente
#   scripts/verify-backup.sh backups/radar-....dump
#
# Um backup que nunca foi restaurado não é um backup.
#
# As credenciais nunca passam pelo shell do host: todo comando de banco roda
# dentro do container, que já tem POSTGRES_USER/POSTGRES_DB no ambiente.
set -euo pipefail

APP_DIR="${APP_DIR:-/srv/seller-radar}"
BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/backups}"

cd "$APP_DIR"

dump="${1:-}"
if [ -z "$dump" ]; then
    dump="$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'radar-*.dump' -printf '%T@ %p\n' \
        | sort -nr | head -1 | cut -d' ' -f2- || true)"
fi

if [ -z "${dump:-}" ] || [ ! -f "$dump" ]; then
    echo "ERRO: nenhum dump encontrado (procurei em $BACKUP_DIR)" >&2
    exit 1
fi

# Nome do banco lido de dentro do container (o .env não chega ao shell do host).
ORIGEM_DB="$(docker compose exec -T db sh -c 'printf %s "$POSTGRES_DB"' | tr -d '\r')"
if [ -z "$ORIGEM_DB" ]; then
    echo "ERRO: POSTGRES_DB não definido no container do banco" >&2
    exit 1
fi

# psql dentro do container, SQL vindo pela entrada padrão (evita aspas aninhadas).
psql_db() {
    local db="$1"
    docker compose exec -T db sh -c \
        "psql --username \"\$POSTGRES_USER\" --dbname \"$db\" --no-psqlrc -tA -v ON_ERROR_STOP=1"
}

scratch="radar_restore_$(date +%s)"
echo "verificando: $dump"
echo "banco descartável: $scratch"

limpar() {
    psql_db postgres > /dev/null 2>&1 <<SQL || true
DROP DATABASE IF EXISTS "$scratch" WITH (FORCE);
SQL
}
trap limpar EXIT

psql_db postgres > /dev/null <<SQL
CREATE DATABASE "$scratch";
SQL

if ! docker compose exec -T db sh -c \
    'pg_restore --username "$POSTGRES_USER" --dbname "'"$scratch"'" --no-owner --no-privileges' \
    < "$dump" > /dev/null; then
    echo "ERRO: pg_restore retornou erro ao restaurar $dump" >&2
    exit 1
fi

# Lista de tabelas do banco de origem.
tabelas="$(psql_db "$ORIGEM_DB" <<'SQL'
select tablename from pg_tables where schemaname = 'public' order by 1;
SQL
)"
tabelas="$(printf '%s\n' "$tabelas" | tr -d '\r' | sed '/^$/d')"

if [ -z "$tabelas" ]; then
    echo "ERRO: banco de origem sem tabelas em public — nada a conferir" >&2
    exit 1
fi

# Uma única consulta por banco, em vez de uma por tabela.
sql=""
for t in $tabelas; do
    sql+="select '$t' as tabela, count(*) as n from \"$t\" union all "
done
sql="${sql% union all }"
sql+=" order by 1;"

contar() {
    psql_db "$1" <<SQL | tr -d '\r' | sed '/^$/d'
$sql
SQL
}

origem="$(contar "$ORIGEM_DB")"
restaurado="$(contar "$scratch")"

if diff <(printf '%s\n' "$origem") <(printf '%s\n' "$restaurado") > /tmp/radar-verify.diff 2>&1; then
    total="$(printf '%s\n' "$origem" | wc -l | tr -d ' ')"
    echo "OK: $total tabelas conferidas, contagens idênticas"
    rm -f /tmp/radar-verify.diff
    exit 0
fi

echo "FALHOU: contagens divergentes (origem | restaurado)" >&2
cat /tmp/radar-verify.diff >&2
rm -f /tmp/radar-verify.diff
exit 1
