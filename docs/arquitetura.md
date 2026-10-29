# Arquitetura

Documento vivo. Cada decisão registra o motivo e o que foi descartado.

## Visão geral

```
Internet ──► Proxy reverso (TLS, já existente) ──► web (FastAPI + Jinja2/HTMX)
                                                     │
                                       rede interna ─┼── db (PostgreSQL 17)
                                                     └── worker (APScheduler)
```

Apenas o serviço `web` entra na rede do proxy reverso. `db` e `worker` ficam em rede
interna, sem portas publicadas no host.

## Decisões

### D0 — Implementação nova, com reaproveitamento pontual

**Contexto:** existia uma implementação anterior de data lake para o Mercado Livre
(~19 mil linhas, 223 de 225 testes passando em execução verificada). Ela foi avaliada
antes de decidir entre adaptar e reescrever.

**Decisão:** construir o Seller Radar do zero e portar apenas três peças isoladas:

1. o *rate limiter* com backoff exponencial e respeito a `Retry-After` (testado);
2. os casos de teste financeiros como referência de borda (o código, não);
3. o padrão de *raw payload* e snapshots para auditoria.

**Motivo:** o que o projeto anterior não tem é justamente o núcleo deste — modelo de
pedidos, itens, pagamentos e envios; visitas por anúncio; autenticação do painel;
criptografia dos tokens; lock de renovação; dinheiro em `NUMERIC`; vigência de custo.
Adaptar significaria reescrever o núcleo mantendo milhares de linhas incompatíveis em
volta.

**Descartado:** adotar o projeto anterior como base, com reuso integral.

### D1 — Interface renderizada no servidor (Jinja2 + HTMX)

**Motivo:** um único usuário, uma VPS pequena e muitos gráficos simples. Server-side
elimina build de frontend, `node_modules`, um processo Node e o problema de manter duas
linguagens. HTMX cobre os pontos interativos (filtros, sincronização manual com progresso).

**Descartado:** SPA (React/Vue) — custo de build e de manutenção desproporcional ao escopo.

### D2 — `web` e `worker` como serviços separados

**Motivo:** requisito explícito de isolamento de falha. O worker roda sincronização,
cálculos e chamadas à Claude API; um travamento nele não pode derrubar o painel.

Ambos usam a mesma imagem e o mesmo código, diferenciados pelo comando de entrada.

### D3 — PostgreSQL dedicado, não reaproveitamento

**Motivo:** o ambiente já tinha uma stack de BaaS com Postgres exposto publicamente e
serviços instáveis. Acoplar o painel a ela aumentaria o raio de falha e misturaria dados
de produtos diferentes. Um Postgres dedicado, sem porta publicada, é mais simples de
proteger e de fazer backup.

### D4 — Agendador dentro do worker (APScheduler) + advisory lock no Postgres

**Motivo:** cron do host exigiria alterar o sistema e não oferece lock transacional.
O advisory lock do Postgres (`pg_try_advisory_lock`) garante que execução agendada e
manual nunca rodem ao mesmo tempo, sem adicionar Redis, fila ou orquestrador.

### D5 — Sem Redis, sem Kubernetes, sem n8n

**Motivo:** nenhuma necessidade técnica que justifique mais um serviço em uma máquina de
1 vCPU. Controle de concorrência via banco; fila implícita via tabela de execuções com
estado e checkpoint.

### D6 — Cliente HTTP somente leitura

**Motivo:** segurança e previsibilidade. O cliente da API do Mercado Livre aceita apenas
`GET`; a única exceção é `POST /oauth/token` (troca de código e refresh). Qualquer tentativa
de `POST`, `PUT`, `PATCH` ou `DELETE` contra a API levanta exceção antes de sair da aplicação,
e há teste automatizado que falha se isso mudar.

### D7 — Tokens criptografados com chave fora do banco

**Motivo:** um dump do banco não deve entregar acesso à conta do vendedor. A chave vem de
variável de ambiente, nunca é gravada em tabela nem versionada.

Renovação com lock e gravação atômica porque o refresh token é de **uso único**: duas
renovações concorrentes com o mesmo token desconectariam a conta.

### D8 — Valores monetários em `NUMERIC`

**Motivo:** `float` acumula erro e corrompe margem. Toda aritmética financeira usa `Decimal`
no Python e `NUMERIC(14,2)` (ou precisão maior em custos unitários) no banco.

## Fluxo de uma execução diária

1. Aquisição do advisory lock (`radar:daily`). Se falhar, encerra sem erro.
2. Verificação do estado da autorização; renovação de token se necessário.
3. Sincronização incremental por data de atualização, com janela de sobreposição.
4. Validação de integridade (contagens, lacunas de data, órfãos).
5. Cálculo dos indicadores (determinístico, testado).
6. Geração do relatório via Claude API com resposta validada por schema.
7. Persistência do relatório e das recomendações.
8. Liberação do lock e gravação do resultado da execução.

Cada etapa grava checkpoint, permitindo retomar sem duplicar dados (upsert idempotente).

## Estados de qualidade do dado

Todo resultado carrega um estado e, quando não for completo, **qual componente falta**:

- `completo` — todos os componentes disponíveis e conferidos.
- `estimado` — algum componente é aproximação (ex.: frete rateado).
- `incompleto` — componente ausente (ex.: SKU sem custo cadastrado).

A interface nunca apresenta número estimado como se fosse exato.

## Decisões da implementação (Fase 1)

### D9 — Sessão em cookie assinado, sem tabela de sessão

**Motivo:** um usuário, uma instância. Uma tabela de sessões adicionaria escrita no banco a cada
requisição e um job de limpeza, sem ganho. O cookie é assinado com `SECRET_KEY` (vem do ambiente),
`HttpOnly` por padrão do `SessionMiddleware`, `SameSite=Lax` e `Secure` em produção.

**Limitação aceita:** encerrar sessão de todos os dispositivos exige trocar `SECRET_KEY`.

### D10 — CSRF explícito em todo formulário

**Motivo:** o cookie de sessão é enviado automaticamente pelo navegador; sem token, um site
externo pode disparar `POST /login` ou `POST /logout`. O token vive na sessão, é comparado com
`secrets.compare_digest` e é exigido em todo `POST`. Não há estado global, não há dependência extra.

### D11 — Limite de tentativas de login em memória

**Motivo:** 5 falhas por IP a cada 15 minutos barram força bruta trivial sem adicionar Redis.
**Limitação aceita:** o contador é por processo. Com mais de uma instância de `web` ele deixa de
ser global — no desenho atual (uma VPS, um processo) é suficiente.

### D12 — Migrações obrigatórias, sem `create_all`

**Motivo:** `Base.metadata.create_all` esconde divergência entre modelo e banco. O container sobe
com o schema antigo até que `alembic upgrade head` rode explicitamente, e a falha é visível.

### D13 — `GET`-only verificado antes do transporte

**Motivo:** a checagem de D6 acontece em `_guard`, **antes** de a requisição existir. Teste com
`MockTransport` verifica que nenhuma chamada chega a ser emitida para `POST`, `PUT`, `PATCH`,
`DELETE`, `HEAD` e `OPTIONS`. A única exceção do projeto é `POST /oauth/token`, isolada em
`seller_radar/ml/oauth.py`.

