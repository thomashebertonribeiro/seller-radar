# Validação da API do Mercado Livre

Regra do projeto: **nada é implementado por suposição**. Cada endpoint entra no código somente
depois de confirmado na documentação oficial e, quando possível, exercitado com chamada real.

- Data da verificação: **29/10/2026**
- Fonte primária: [developers.mercadolivre.com.br](https://developers.mercadolivre.com.br/pt_br/api-docs-pt-br)
- Legenda de status: `confirmado` · `pendente` · `indisponível`

## OAuth 2.0 — confirmado

Fonte: [`/pt_br/autenticacao-e-autorizacao`](https://developers.mercadolivre.com.br/pt_br/autenticacao-e-autorizacao)
(última atualização declarada na página: 29/12/2025).

| Item | Resultado | Implicação no projeto |
|---|---|---|
| Duração do access token | `expires_in = 21600` (6 horas) | renovar antes do vencimento, não a cada requisição |
| Refresh token | **uso único**, rotacionado a cada renovação | lock obrigatório + gravação atômica do par |
| Validade do refresh token | 6 meses | monitorar idade e alertar antes de expirar |
| Apenas o último refresh vale | sim | perder um refresh rotacionado = desconexão |
| PKCE | opcional, habilitado na configuração do aplicativo | se habilitado, `code_challenge`/`code_verifier` passam a ser **obrigatórios**; suporta `S256` e `plain` |
| `state` | recomendado pela doc | o Mercado Livre **não valida** — a validação é nossa: aleatório, uso único, com expiração |
| `redirect_uri` | precisa ser idêntica à cadastrada, sem parâmetros variáveis | callback fixo em HTTPS |
| Usuário operador/colaborador | grant falha com `invalid_operator_user_id` | exigir conta administradora |
| Invalidação antecipada | troca de senha, atualização do client secret, revogação, 4 meses sem uso | tratar como `invalid_grant` e pedir reconexão |
| Escopos | `offline_access`, `read`, `write` | usar `offline_access read` no modo somente leitura |

Códigos de erro documentados: `invalid_client`, `invalid_grant`, `invalid_scope`,
`invalid_request`, `unsupported_grant_type`, `forbidden` (403), `local_rate_limited` (429),
`unauthorized_client`, `unauthorized_application`.

**Consequência de projeto:** o fluxo de renovação é o ponto mais frágil da integração.
Duas renovações concorrentes com o mesmo refresh token derrubam a conexão. Por isso:
`SELECT ... FOR UPDATE` na linha da integração + advisory lock por conta + gravação do novo
par na mesma transação.

**Implementado na Fase 2:** `state` de uso único no Postgres (D14), PKCE `S256` com verificador
cifrado (D15) e renovação sob advisory lock com `needs_reconnect` em erro terminal (D16) —
ver `docs/arquitetura.md` e `src/seller_radar/ml/credentials.py`.

## Validação com conta real (Fase 3a) — 29/10/2026

Conta `BRASILICES` (seller `2617251613`, site `MLB`), token de produção, **apenas `GET`**.
Números medidos, não estimados: 56 anúncios (25 ativos), 14 pedidos em 30 dias.

| Recurso | Resultado | Implicação |
|---|---|---|
| `GET /users/{id}/items/search` | OK — `paging.total/limit/offset`, filtro `status` | paginação explícita; sincronizar por `date_last_updated` |
| `GET /items/{id}` | OK — `price`, `available_quantity`, `sold_quantity`, `status`, `category_id`, `listing_type_id`, `condition`, `catalog_listing`, `variations`, `last_updated` | base do cadastro de anúncios |
| `GET /items?ids=` (multiget) | OK — lista de `{code, body}` | lote de até 20 por chamada reduz o custo da sincronização |
| `GET /items/{id}/visits/time_window` | OK — `last=30&unit=day`, pontos `{date, total, visits_detail}` | visitas diárias por anúncio |
| `GET /sites/MLB/listing_prices` | OK — **retorna lista**; `sale_fee_details` = `{percentage_fee, fixed_fee, gross_amount}` | tarifa de referência: `gold_special` 11%, `gold_pro` 16% |
| `GET /orders/search` | OK — filtros `order.date_created.from` **e** `order.date_last_updated.from`; `sort=date_desc` | janela de 30 dias funcionou; incremental por atualização |
| `GET /orders/{id}` | OK — `order_items[].sale_fee`, `payments[]`, `taxes`, `shipping.id` | **tarifa real da venda** vem em `sale_fee` |
| `GET /shipments/{id}` | OK — `base_cost`, `cost_components`, `logistic_type`, `shipping_option` | frete e modalidade logística |
| `GET /shipments/{id}/costs` | OK — `gross_amount`, `senders[].cost`, `discounts`, `save` | **custo real do frete do vendedor** |
| Product Ads | **HTTP 401** — `Operator User is not authorized to access the advertiser` | sem acesso com este token; fica `indisponível` até haver permissão |
| Notificações | webhook ativo em `/webhooks/mercadolivre`, resposta 200 | tópicos aceitos; o corpo **não** é processado (D17) |
| Dados do comprador | `buyer` traz **apenas** `id` e `nickname` | suficiente para distinguir recompra; nada de endereço ou e-mail |

### Descobertas que mudam o modelo de dados

1. **`orders[].shipping_cost` e `orders[].taxes.amount` vêm `null`.** O custo do frete está em
   `/shipments/{id}/costs` (`senders[].cost`) e a tarifa do ML em `order_items[].sale_fee`.
   Modelar frete a partir do pedido daria sempre zero.
2. **`payments[].marketplace_fee` vem `0.0`** no caso medido (`bank_transfer`). Não serve como
   fonte de tarifa; `sale_fee` é a fonte correta.
3. **`sale_fee` é por item de pedido**, não por pedido — pedidos com vários vendedores/itens
   exigem soma por linha. *(Refinado na Fase 3b: é por **unidade**, não por linha — ver abaixo.)*
4. **`seller_custom_field` pode ser `null`**; o SKU também aparece em `order_items[].item.seller_sku`.
   O vínculo anúncio↔custo precisa aceitar SKU ausente sem quebrar.
5. **Visitas só existem para anúncio ativo** — o anúncio encerrado devolveu `total_visits: 0` e
   nenhum ponto. Ausência de dado não é zero: a tela precisa distinguir "sem visitas" de
   "anúncio sem coleta".

Enquanto um recurso estiver `pendente` ou `indisponível`, a tela correspondente mostra
**"indisponível"** — nunca um número estimado apresentado como real.

## Sincronização real (Fase 3b) — 29/10/2026

Mesma conta, token de produção, **apenas `GET`**. Tudo abaixo foi medido executando a coleta
contra a API, não lido em documentação.

### Resultado da primeira coleta

| Medida | Valor |
|---|---|
| Anúncios | 56 (25 ativos, 28 pausados, 2 em revisão, 1 encerrado) |
| Pedidos | 48, de 03/02/2026 a 28/10/2026 |
| Linhas de pedido / pagamentos | 48 / 49 |
| Envios | 34 |
| Dias de visita coletados | 144 (somente anúncios ativos) |
| Cobertura de visitas | 25 autoritativas, 31 não autoritativas |
| Chamadas à API | 98 em 36 s, **zero avisos** |

### Limites e formatos medidos

| Fato | Evidência | Consequência |
|---|---|---|
| `visits/time_window` aceita no máximo **150 dias** | `last=365` → HTTP 400 `should be smaller or equal to 150 days` | janela de visitas fixada em 150; acima disso o provedor recusa |
| `sale_fee` é **por unidade** | mesmo anúncio com `quantity` 1 e 2 devolve `4.58` nos dois | coluna `sale_fee_unit`; a tarifa da linha é o produto pela quantidade |
| `/orders/search` devolve todo o histórico com `-365d` e `-730d` (48 nos dois) | contagem idêntica | janela inicial de 365 dias cobre a loja inteira |
| O envio declara **um** `order_id` | 16 envios em 60 dias, nenhum com dois pedidos | rateio acontece dentro de um pedido, nunca entre pedidos |
| `shipping_mode` vem `null`; a modalidade está em `mode` (`me2`) | payload de `/shipments/{id}` | ler `mode` como alternativa |
| `cost_components` vem com números JSON | `{"ratio": 8.25, "loyal_discount": 0.3, ...}` | gravado como texto decimal exato; não entra em cálculo |
| `seller_cost = 0.00` é **valor real**, não ausência | 10 envios com `senders[].cost = 0` e `cost_components` zerado (comprador paga o frete) | zero explícito é zero; ausência do remetente é que vira `NULL` |
| `payments[].total_paid_amount` **inclui o frete pago pelo comprador** | pedido de `total_amount` 7,99 com pagamento de 16,98 (= 7,99 + 8,99) | pagamento não é receita; receita é a soma das linhas |
| 12 pedidos com a tag `no_shipping` não têm `shipping_id` (3 deles `paid`) | `shipping.id` ausente, tag presente | frete fica `NULL` (desconhecido), não zero — nada declara o custo |
| Todo pedido desta conta tem **uma única linha** | 48 pedidos, 48 linhas | o rateio de frete nunca foi marcado como estimado aqui; o caminho multi-linha existe e é testado |
| `domain_id` é um slug longo | `MLB-MANUAL_GARLIC_PRESSES_AND_CHOPPERS` (38 caracteres), 18 anúncios acima de 24 | coluna virou `text` na migração `0005` |

### Incidente do primeiro sync — `varchar(24)`

A primeira execução em produção falhou em `INSERT INTO items` com
`value too long for type character varying(24)`. O `domain_id` real tem 38 caracteres. A
medição de todos os campos de texto (56 anúncios, 48 pedidos, 34 envios) mostrou que **só essa
coluna** passava do limite — as demais cabiam com folga (`status` 12/24, `category_id` 9/24,
`listing_type_id` 12/24, `condition` 4/24, `payment_method_id` 16/32, `logistic_type` 12/32).
Por isso a correção alargou apenas `items.domain_id`, com medição registrada na migração.

## Limites de requisição

A documentação consultada cita `429 local_rate_limited` sem publicar uma cota numérica
estável. Decisão: tratar 429 e 5xx com backoff exponencial + jitter, respeitar `Retry-After`
quando presente, e **não** repetir erros 4xx definitivos. A cota real será medida com chamadas
legítimas — as ~20 chamadas da Fase 3a não produziram nenhum 429.

## Notas

- Endpoints públicos (busca de itens, categorias) alimentariam o módulo de inteligência de
  mercado, mas **não serão implementados nesta versão**. A tela existe e informa que a fonte
  de dados não foi implementada.
- Qualquer divergência entre este documento e o comportamento observado será registrada aqui
  antes de mudar o código.
