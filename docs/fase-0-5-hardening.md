# Fase 0.5 — Contenção da superfície exposta

Data: 29/10/2026 · Executado via SSH com usuário dedicado `radar` (sudo + grupo `docker`).

Objetivo: fechar os riscos 1 e 2 do [diagnóstico da Fase 0](diagnostico-fase-0.md) **antes** de
implantar qualquer serviço novo. Nenhuma linha do Seller Radar foi publicada nesta etapa.

## Estado encontrado

| Item | Antes |
|---|---|
| UFW | `inactive`; política `INPUT` em `ACCEPT` |
| `DOCKER-USER` | vazia — nenhum filtro sobre tráfego de containers |
| Portas públicas | 22, 80, 81, 443, **32768** (n8n), **32769** (Postiz), **32771** (Evolution) |
| `sshd` | `PermitRootLogin yes` + `PasswordAuthentication yes` |

## Causa raiz: Docker ignora o UFW

O Docker grava regras próprias nas chains `DOCKER`/`FORWARD` e faz DNAT em `PREROUTING`. O UFW
filtra apenas a chain `INPUT`. Resultado: habilitar o UFW e considerar o problema resolvido
deixaria 32768, 32769, 32771 e 81 abertos para a internet.

Além disso, os compose de n8n, Postiz e Evolution declaravam `ports:` com a sintaxe curta
(`- "5678"`), sem IP de origem. Nessa forma o Docker escolhe uma porta aleatória e publica em
`0.0.0.0` — a origem dos 3276x.

**Decisão:** não combater o Docker com regras em `DOCKER-USER`. Republicar as portas em
`127.0.0.1`, que é declarativo, sobrevive a reboot e não depende de `conntrack`.

## Mudanças aplicadas

Cada arquivo teve backup `.bak-<timestamp>` no mesmo diretório antes da edição.

| Arquivo | Antes | Depois |
|---|---|---|
| `/docker/n8n-fhmf/docker-compose.yml` | `- "5678"` | `- "127.0.0.1:5678:5678"` |
| `/docker/postiz-law2/docker-compose.yml` | `- "5000"` | `- "127.0.0.1:5000:5000"` |
| `/docker/evolution-api-vxbz/docker-compose.yml` | `- "8080"` | `- "127.0.0.1:8080:8080"` |
| `/opt/npm/docker-compose.yml` | `- "81:81"` | `- "127.0.0.1:81:81"` |

Firewall do host:

```
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
```

Acesso administrativo passou a ser por túnel SSH:

```bash
ssh -L 8081:127.0.0.1:81 radar-vps     # Nginx Proxy Manager
ssh -L 5678:127.0.0.1:5678 radar-vps   # n8n
```

## Efeito colateral encontrado e corrigido

n8n e Evolution estavam na rede `proxy` (usada pelo proxy reverso, que resolve upstream **por
nome de container**) através de `docker network connect` manual, fora do compose. A recriação
dos containers removeu essa conexão e os três domínios passaram a responder `502`.

Correção permanente nos dois compose:

```yaml
    networks:
      - default
      - proxy

networks:
  proxy:
    external: true
```

**Lição:** qualquer container que o proxy reverso referencia por nome precisa declarar a rede
no compose. Conexão manual não sobrevive a `docker compose up -d`.

## Validação

| Verificação | Antes | Depois |
|---|---|---|
| 81, 32768, 32769, 32771 (externo) | abertas | **fechadas** |
| 22, 80, 443 (externo) | abertas | abertas |
| UFW | inativo | **ativo**, 22/80/443 |
| `n8n`, `n8n-fhmf`, `evolution` | 200 | 200 |
| `links` | 200 | 200 |
| `extrato` | 401 | 401 |
| `postiz-law2` | 307 | 307 |
| `ml`, `financaspessoas`, `opencode`, `supabase` | 502 | 502 (já quebrados antes) |

## Restrição operacional observada

Após sequência de conexões SSH em curto intervalo, a porta 22 passou a responder timeout para o
IP de origem, voltando ao normal em ~5 minutos. Não existe regra correspondente em `iptables`
nem `fail2ban` instalado; o agente `monarx-agent` está ativo. A causa provável é filtro externo
(agente de segurança ou proteção de rede do provedor).

Consequência prática: operar em **sessões agrupadas**, não em conexões curtas e repetidas.
Multiplexação (`ControlMaster`) não está disponível no cliente OpenSSH do Windows usado.

## Pendências

1. **Endurecer SSH**: `PermitRootLogin no` e `PasswordAuthentication no`. Só depois de confirmar
   o console do provedor como via de emergência.
2. **Quatro proxy hosts órfãos** (`ml`, `financaspessoas`, `opencode`, `supabase`) apontando para
   containers que não existem. `ml.brasilices.tech` é o candidato natural ao `ML_REDIRECT_URI`
   do Seller Radar — precisa ser repontado, não removido.
3. **Redes órfãs** `dashboard_financas_default` e `supabase_default`, sem containers.
4. **Firewall do provedor** segue sem regras. Deliberado: aplicar regra errada ali pode custar o
   acesso sem rollback pelo SSH.

## Rollback

```bash
# portas: restaurar o backup e recriar
cd /docker/n8n-fhmf && sudo cp docker-compose.yml.bak-<ts> docker-compose.yml && sudo docker compose up -d

# firewall
sudo ufw disable
```
