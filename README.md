# Seller Radar Intelligence

Painel privado de análise de uma loja do Mercado Livre (site MLB, Brasil), com integração
**somente leitura** à API oficial, motor financeiro determinístico e recomendações geradas
pela Claude API a partir de indicadores já calculados.

> **Status:** Fase 0 (diagnóstico) e Fase 0.5 (contenção da superfície exposta) concluídas.
> Fase 1 em andamento: esqueleto executável com login próprio, migrações, separação `web`/`worker`
> e cliente da API do Mercado Livre **somente leitura**, com teste que falha se a regra mudar.
> Nenhum indicador financeiro é exibido ainda.

## Problema

O painel do vendedor mostra pedidos e visitas, mas não responde à pergunta que importa:
**quanto cada venda realmente deixa**. Tarifa, frete, imposto, custo do produto e publicidade
estão espalhados, e a margem real de cada anúncio fica invisível.

## Escopo

- Login próprio, uma única conta de vendedor, um único usuário.
- OAuth 2.0 com a API oficial do Mercado Livre (Brasil).
- Sincronização de anúncios, pedidos, pagamentos, envios e visitas.
- Cadastro de custos por SKU (manual e CSV) com vigência por data.
- Motor financeiro determinístico: receita bruta, valor líquido, margem de contribuição,
  ponto de equilíbrio e preço mínimo para margem-alvo.
- Relatórios diários com recomendações priorizadas da Claude API.
- Rotina diária automática, independente de sessão interativa.

## Fora de escopo (por decisão)

- **Escrita** na conta do Mercado Livre: o cliente HTTP só permite `GET`, exceto o endpoint
  de token do OAuth. Nenhuma alteração de anúncio, preço, estoque, campanha ou mensagem.
- **Scraping** como substituto da API oficial.
- Cálculo de **lucro contábil**. O sistema calcula margem de contribuição, não resultado contábil.
- Monitoramento de concorrentes com dados inventados: a tela existe, a fonte de dados não.

## Arquitetura

```
Internet ──► Proxy reverso (TLS) ──► web (FastAPI + Jinja2/HTMX)
                                        │
                          rede interna ─┼── db (PostgreSQL 17, sem porta pública)
                                        └── worker (APScheduler, cálculos, relatórios)
```

- **Interface renderizada no servidor** (Jinja2 + HTMX): um usuário, uma VPS modesta,
  sem build de frontend e sem um processo a mais.
- **`web` e `worker` separados**: falha do worker não derruba o painel; falha da IA não
  invalida a sincronização.
- **PostgreSQL dedicado** com migrações Alembic; concorrência controlada por *advisory lock*
  no próprio banco — sem Redis.
- **Tokens do Mercado Livre criptografados em repouso**, chave fora do banco.
- **Logs estruturados em JSON** com `run_id` por execução.

Detalhes e justificativas em [`docs/arquitetura.md`](docs/arquitetura.md).

## Documentação

| Documento | Conteúdo |
|---|---|
| [`docs/arquitetura.md`](docs/arquitetura.md) | Decisões de arquitetura e trade-offs |
| [`docs/diagnostico-fase-0.md`](docs/diagnostico-fase-0.md) | Inventário do ambiente e riscos encontrados |
| [`docs/fase-0-5-hardening.md`](docs/fase-0-5-hardening.md) | Fechamento das portas expostas, firewall e efeitos colaterais |
| [`docs/api-validation.md`](docs/api-validation.md) | O que foi confirmado na documentação oficial e o que falta verificar |
| [`docs/formulas.md`](docs/formulas.md) | Fórmulas financeiras com exemplo numérico |
| [`docs/operacao.md`](docs/operacao.md) | Runbook de operação (em construção) |

## Como executar

Requisitos: Docker e Docker Compose. Nada de Node, nada de build de frontend.

```bash
cp .env.example .env          # preencha os valores; gere os segredos localmente
docker compose up -d --build
docker compose exec web alembic upgrade head
docker compose exec web python -m seller_radar.cli create-user --email voce@exemplo.com
```

A aplicação sobe em `http://127.0.0.1:8010` — publicada **apenas no loopback**. O TLS e o
domínio ficam no proxy reverso do servidor.

Suíte de testes (roda em container separado, sem Node e sem banco local):

```bash
docker compose --profile test run --rm test
```

Gerar os segredos:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"                    # SECRET_KEY
python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"  # TOKEN_ENCRYPTION_KEY
```


## Segurança

- Nenhum segredo neste repositório. Variáveis em [`.env.example`](.env.example), sem valores.
- Dados pessoais de compradores são minimizados por padrão (LGPD).
- Ver [`SECURITY.md`](SECURITY.md).

## Licença

Sem licença definida até o momento — todos os direitos reservados ao autor.
