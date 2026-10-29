# Operação

> Em construção. Este documento será completado na Fase 6, após o sistema estar em execução.

## Estado atual

Nenhum serviço do Seller Radar está implantado. O que existe hoje está descrito em
[`diagnostico-fase-0.md`](diagnostico-fase-0.md) e [`arquitetura.md`](arquitetura.md).

## O que será documentado aqui

1. Instalação e primeira execução (Compose, migrações, usuário inicial).
2. Variáveis de ambiente e rotação de segredos.
3. Conectar e reconectar a conta do Mercado Livre.
4. Sincronização manual e agendada; como interpretar o `run_id` nos logs.
5. Backup: o que é feito, onde fica, retenção — e **procedimento de restauração testado**.
6. Diagnóstico de falhas comuns (`invalid_grant`, 429, banco indisponível, falha da IA).
7. Atualização de versão e rollback.

## Princípios

- Nenhum segredo em log ou em documento.
- Toda execução tem `run_id` rastreável do início ao fim.
- Falha da IA não invalida a sincronização; falha da sincronização não derruba o painel.
- Restauração de backup só é considerada válida depois de executada de verdade.
