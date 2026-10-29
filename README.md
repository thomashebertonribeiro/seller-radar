# Seller Radar Intelligence

Painel privado de análise de uma loja do Mercado Livre (site MLB, Brasil), com integração
**somente leitura** à API oficial, motor financeiro determinístico e recomendações geradas
pela Claude API a partir de indicadores já calculados.

> **Status:** Fase 0 (diagnóstico de infraestrutura) concluída. Nenhum código de aplicação
> implementado ainda. Este repositório contém, por enquanto, apenas a documentação de base,
> as decisões de arquitetura e a validação inicial da API.

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
| [`docs/api-validation.md`](docs/api-validation.md) | O que foi confirmado na documentação oficial e o que falta verificar |
| [`docs/formulas.md`](docs/formulas.md) | Fórmulas financeiras com exemplo numérico |
| [`docs/operacao.md`](docs/operacao.md) | Runbook de operação (em construção) |

## Como executar

Ainda não há código executável. Este documento será atualizado na Fase 1, junto com o
`docker-compose.yml`, as migrações e o comando de criação do usuário inicial.

## Segurança

- Nenhum segredo neste repositório. Variáveis em [`.env.example`](.env.example), sem valores.
- Dados pessoais de compradores são minimizados por padrão (LGPD).
- Ver [`SECURITY.md`](SECURITY.md).

## Licença

Sem licença definida até o momento — todos os direitos reservados ao autor.
