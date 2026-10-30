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

### D0 — Reaproveitamento da implementação existente

**Contexto:** duas implementações existem. Uma anterior, de *data lake* para o Mercado
Livre (~19 mil linhas, 223 de 225 testes passando em execução verificada), focada em
preço, concorrência e sinais de mercado. E a atual, neste repositório, já com Fase 0.5
(hardening) e Fase 1 (login, banco, painel, finance engine) publicadas.

**Decisão:** o Seller Radar **continua a partir do código deste repositório**, sem
reescrita. A implementação anterior não é incorporada; serve apenas como referência em
três pontos: *rate limiter* com backoff/`Retry-After`, casos de teste financeiros de
borda e o padrão de *raw payload* para auditoria.

**Motivo:** a base atual já resolve o que a anterior não tinha — criptografia dos tokens,
`Decimal` de ponta a ponta, guarda GET-only testada, autenticação do painel, migrações
versionadas e testes verdes. Reescrever custaria mais do que evoluir.

**Descartado:** incorporar o *data lake* anterior (sem modelo de pedidos, sem visitas,
sem autenticação, com dinheiro em `float` e dependência de Redis/Playwright).

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

## Decisões da implementação (Fase 2)

### D14 — `state` do OAuth no banco, de uso único e amarrado ao usuário

**Motivo:** a documentação do Mercado Livre deixa claro que o `state` **não é validado pelo
provedor** — a proteção é inteiramente nossa. Guardar em memória seria frágil: um restart do
`web` no meio do fluxo invalidaria uma autorização legítima, e um processo com mais de um worker
não veria o `state` do outro. A linha em `oauth_states` resolve os dois casos e permite consumo
atômico.

**Como:** o consumo é um único `UPDATE ... RETURNING` com as condições
`consumed_at IS NULL AND expires_at > now() AND user_id = :sessão`. Não há janela entre checar e
marcar — duas requisições simultâneas com o mesmo `state` produzem **uma** troca de código.
Validade de 10 minutos; linhas vencidas há mais de um dia são apagadas na próxima tentativa.

**Amarração ao usuário:** um `state` capturado por um terceiro não serve na sessão de outra
pessoa. Isso fecha o ataque de *login CSRF* (induzir a vítima a conectar a conta do atacante).

### D15 — PKCE com `S256`, verificador cifrado

**Motivo:** o Mercado Livre aceita PKCE opcional. Ligar o `S256` custa uma coluna e elimina o
valor de um `code` interceptado: sem o verificador, o código não vira token. O verificador é
gravado cifrado com a mesma chave dos tokens (D7), porque é um segredo de uso único tanto quanto
o próprio `code`.

### D16 — Renovação serializada, com falha terminal explícita

**Motivo:** o refresh token é de uso único e só o último vale (ver `docs/api-validation.md`).
Duas renovações concorrentes derrubam a conexão de forma irreversível.

**Como:** o caminho rápido (token com mais de 5 minutos de vida) não pega lock. Quando é preciso
renovar, a execução pega `pg_advisory_xact_lock(namespace, user_id)` e **relê** a credencial com
`SELECT ... FOR UPDATE`. Quem esperou encontra o token já renovado e não renova de novo. O novo
par é gravado na mesma transação — ou os dois tokens mudam, ou nenhum.

**Falha terminal:** `invalid_grant`, `invalid_client` e `invalid_token` marcam a credencial com
`needs_reconnect` e o motivo em `last_error`, em vez de tentar de novo a cada requisição. O painel
mostra "conectar novamente" e não insiste com o provedor.

### D17 — Webhook existe para satisfazer o DevCenter, não para processar

**Contexto:** o DevCenter não salva a aplicação com a URL de notificações vazia. Mas a
sincronização da Fase 2 não depende de notificações.

**Decisão:** `POST /webhooks/mercadolivre` responde 200 e **não faz nada** além de registrar o
tópico. Nenhuma escrita, nenhuma consulta à API no caminho da requisição.

**Motivo:** o provedor exige HTTP 200 em até 500 ms e retenta por 1 hora; se continuar falhando,
**desativa o tópico** na aplicação. Processar no caminho da resposta criaria um modo de falha
silencioso e difícil de diagnosticar, sem nenhum benefício enquanto o volume é baixo.

**Consequência:** quando o processamento por notificação existir, o corpo será enfileirado — a
resposta continua imediata e o trabalho acontece fora do ciclo. Tópicos de interesse já são
reconhecidos, então a mudança será local. Até lá, a coleta diária não depende de webhook.

### D18 — Cobertura de visitas explícita: ausência nunca é zero

**Contexto:** o provedor só devolve dias com visita, e só para anúncio ativo (medido: 150 dias
é o teto da janela). Uma tabela de visitas sozinha não distingue "zero visitas" de "nunca
coletado" nem de "anúncio encerrado".

**Decisão:** `item_visits` guarda os pontos diários e `item_visit_coverages` registra, por
anúncio e por janela, o intervalo consultado, o status do anúncio no momento e se a cobertura é
**autoritativa**.

**Como:** `authoritative` só é verdadeiro quando o anúncio estava ativo e a API devolveu o
intervalo. Nesse caso a janela substitui os dados anteriores — uma correção do provedor para
baixo precisa valer. Para anúncio inativo não há chamada: registra-se a cobertura sem autoridade
e o histórico coletado enquanto estava ativo é preservado.

### D19 — Frete: custo do vendedor em `/costs`, desconhecido não é zero

**Decisão:** `shipments.seller_cost` é a soma de `senders[].cost` das entradas do nosso
vendedor em `/shipments/{id}/costs`. Quando ele não aparece, o valor é `NULL`.

**Motivo:** `orders[].shipping_cost` vem sempre nulo (medido) e `base_cost` é a referência do
provedor, não o que o vendedor pagou. Zero é um valor legítimo — 10 envios da conta têm
`senders[].cost = 0` porque o comprador paga o frete. Tratar "não aparece" como zero faria a
margem parecer melhor do que é; tratar zero como desconhecido esconderia frete grátis real.

**Rateio:** o envio pertence a um pedido, mas pode cobrir várias linhas. O custo é rateado
proporcionalmente ao valor da linha (`unit_price * quantity`) e marcado `freight_estimated`
quando há mais de uma linha: o total fecha, a divisão exata entre itens é desconhecida. Pedido
com uma linha recebe o valor integral, sem marcação. Envio sem custo conhecido não gera rateio
(as linhas ficam `NULL`), e a tela mostra "frete não informado".

### D20 — Receita vem das linhas do pedido, nunca dos pagamentos

**Medido:** `total_paid_amount` inclui o frete pago pelo comprador (pedido de 7,99 com pagamento
de 16,98) e pedidos cancelados aparecem com pagamento estornado. `marketplace_fee` vem `null` ou
`0.0` e não serve como tarifa.

**Decisão:** receita = `Σ unit_price * quantity` das linhas não canceladas; tarifa =
`Σ sale_fee_unit * quantity`. `payments` é gravado para auditoria e conciliação, não para
cálculo. `total_amount` do pedido é conferência: na conta real ele sempre bateu com a soma das
linhas.

**Consequência:** nenhum indicador financeiro pode ser derivado de `payments` sem antes explicar
a diferença de frete — o que a tabela de pagamentos não permite separar.

### D21 — Tamanho de coluna vem de medição, não de estimativa

**Contexto:** o primeiro sync real morreu em `varchar(24)` porque `domain_id` é um slug de
taxonomia com 38 caracteres. Nenhum teste local pegaria isso: os payloads de teste eram curtos.

**Decisão:** campos de texto do provedor só recebem limite depois de medir o maior valor real.
Slug de taxonomia sem tamanho documentado vai para `text` — o próximo domínio mais longo não
pode derrubar a coleta inteira. Identificadores com formato estável (`MLB` + dígitos) mantêm
limite, que serve de validação.

**Como fica testado:** o valor real de 38 caracteres é gravado no Postgres pela suíte de
integração, e a coleta tem teste unitário garantindo que não trunca o slug.
