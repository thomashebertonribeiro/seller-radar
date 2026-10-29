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

## A validar na Fase 2

| Recurso | O que precisa ser confirmado | Status |
|---|---|---|
| `GET /orders/search` | filtros por data de criação e de atualização; limite de `offset`; tamanho máximo de página; janela de antiguidade consultável | pendente |
| `GET /orders/{id}` | composição de tarifa por item, descontos, cancelamentos, mediações | pendente |
| `GET /shipments/{id}` | custo do envio, quem paga o frete, rateio entre itens | pendente |
| `GET /items/{id}` e variações | campos de estoque, status, preço, SKU (`seller_custom_field`) | pendente |
| `GET /items/{id}/visits/time_window` | granularidade, limite de dias por consulta, disponibilidade histórica | pendente |
| Product Ads | se a conta tem acesso e qual permissão é exigida | pendente |
| Faturamento / cobranças | existência de endpoint e escopo necessário | pendente |
| Notificações (webhooks) | tópicos disponíveis, validação de origem, reentrega | pendente |
| Dados do comprador | o que a API ainda expõe e sob quais condições | pendente |

Enquanto um recurso estiver `pendente`, a tela correspondente mostra **"indisponível"** —
nunca um número estimado apresentado como real.

## Limites de requisição

A documentação consultada cita `429 local_rate_limited` sem publicar uma cota numérica
estável. Decisão: tratar 429 e 5xx com backoff exponencial + jitter, respeitar `Retry-After`
quando presente, e **não** repetir erros 4xx definitivos. A cota real será medida na Fase 2
com chamadas legítimas.

## Notas

- Endpoints públicos (busca de itens, categorias) alimentariam o módulo de inteligência de
  mercado, mas **não serão implementados nesta versão**. A tela existe e informa que a fonte
  de dados não foi implementada.
- Qualquer divergência entre este documento e o comportamento observado será registrada aqui
  antes de mudar o código.
