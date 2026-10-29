#!/usr/bin/env bash
# Backup lógico do Postgres do Seller Radar.
#
# Roda no host (systemd timer), mas executa pg_dump DENTRO do container do banco:
# nada de credencial em linha de comando no host e nada instalado além do que já existe.
#
# Saída: $BACKUP_DIR/radar-AAAAMMDD-HHMMSS.dump (formato custom, comprimido)
# Retenção: $BACKUP_RETENTION_DAYS dias (padrão 14)
set -euo pipefail

APP_DIR="${APP_DIR:-/srv/seller-radar}"
BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"

cd "$APP_DIR"
umask 077
mkdir -p "$BACKUP_DIR"

# Impede duas execuções simultâneas (timer + execução manual).
exec 9>"$BACKUP_DIR/.lock"
if ! flock -n 9; then
    echo "outro backup em andamento; saindo"
    exit 0
fi

ts="$(date +%Y%m%d-%H%M%S)"
tmp="$BACKUP_DIR/.radar-$ts.dump.part"
final="$BACKUP_DIR/radar-$ts.dump"

cleanup() { rm -f "$tmp"; }
trap cleanup EXIT

# O container já tem POSTGRES_USER/POSTGRES_DB no ambiente.
docker compose exec -T db sh -c \
    'pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --format=custom --compress=9' \
    > "$tmp"

# Só publica o arquivo depois de conferir que ele é um dump legível.
if ! docker compose exec -T db pg_restore --list < "$tmp" > /dev/null; then
    echo "ERRO: dump gerado não pôde ser lido por pg_restore" >&2
    exit 1
fi

mv "$tmp" "$final"
chmod 600 "$final"

# Retenção: remove dumps mais antigos que o limite.
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'radar-*.dump' -mtime +"$RETENTION_DAYS" -delete

tamanho="$(du -h "$final" | cut -f1)"
echo "backup ok: $final ($tamanho)"
