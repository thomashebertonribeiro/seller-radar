# Diagnóstico da infraestrutura — Fase 0

Data: 29/10/2026 · Executado apenas com comandos de leitura, exceto a limpeza autorizada
explicitamente (registrada no fim).

Identificadores do servidor (IP, domínio, hostname) foram omitidos deste documento porque
o repositório é público.

## Ambiente encontrado

| Item | Valor |
|---|---|
| Provedor / plano | VPS KVM 1 |
| Sistema | Ubuntu 24.04.4 LTS, kernel 6.8 |
| CPU | 1 vCPU |
| Memória | 4 GB (3,9 GB utilizáveis) |
| Disco | 48 GB |
| Swap | 2 × 2 GB (arquivos), `swappiness=10` |
| Docker | 29.7.2, 8 projetos Compose |
| Proxy reverso | Nginx Proxy Manager (80/443/81) |
| Firewall de host | inativo (UFW desligado, sem regras em nftables/iptables) |
| Firewall do provedor | criado, sem regras, não sincronizado |

## Constatações

### 1. Disco em 92% (4,1 GB livres)

Causa principal: 27,45 GB em imagens Docker, das quais **9,6 GB eram de uma stack de BaaS
que não estava em uso**. O restante era build cache (653 MB) e volumes.

### 2. `load average` alto sem CPU saturada

Medição com `vmstat`:

```
r  b   swpd    free    buff   cache   si  so   us sy id wa
1  1  1886196  835904  575084 1323612   3   7   18 11 67  0
0  1  1886196  835904  575084 1323612   4   0    3  2  0 75
```

- `us` entre 3% e 18%, `id` entre 66% e 78% → CPU ociosa.
- `wa` entre 15% e 19% e `b` = 1 → **iowait**.
- 1,8 GB em swap → a máquina estava paginando para um disco já cheio.

Conclusão: o gargalo é **memória + I/O**, não processamento. Consequência prática: o
`sshd` ficava lento para enviar o banner e conexões eram derrubadas por timeout.

### 3. Superfície exposta desnecessária

Portas publicadas na internet incluíam o Postgres do BaaS (5432 e 6543), o painel
administrativo do proxy (81), além de portas altas de ferramentas internas. Sem firewall
de host ou de nuvem ativo, tudo isso era alcançável.

### 4. Projeto legado com dependência oculta

Um dashboard financeiro pessoal publicado em subdomínio próprio dependia do BaaS que
seria desligado, e trazia um **token de acesso pessoal do GitHub embutido na URL do remote
git** — segredo exposto em texto claro no servidor.

### 5. Nenhuma implementação anterior aproveitável

Havia um projeto Node apenas com `package.json` e `node_modules`, sem código-fonte, e um
fragmento de rota OAuth do Mercado Livre dentro do dashboard legado. Nada com arquitetura,
testes ou modelo de dados que justificasse reaproveitamento.

## Ações autorizadas e executadas

| Ação | Resultado |
|---|---|
| Backup lógico do BaaS (`pg_dumpall` comprimido, verificado) | 107 KB |
| `docker compose stop` da stack de BaaS | 12 containers parados |
| `docker compose down --rmi all` | 12 imagens removidas, 9,6 GB liberados |
| `docker builder prune` | 653 MB liberados |
| `docker compose stop` do dashboard legado | 2 containers parados |

Antes → depois:

| Métrica | Antes | Depois |
|---|---|---|
| Disco livre | 4,1 GB (92% usado) | **14 GB (72% usado)** |
| RAM disponível | 1.349 MB | **2.343 MB** |
| Containers ativos | 27 | **13** |
| Postgres/portas do BaaS | expostos | **fechados** |

Os volumes de dados do BaaS foram preservados (< 1 MB no total) e a stack pode ser religada
com `docker compose up -d`, rebaixando as imagens.

## Riscos que permanecem

1. **Sem firewall de host.** Portas de ferramentas internas continuam publicadas.
2. **Painel do proxy reverso acessível publicamente** na 81.
3. **Token do GitHub exposto** no remote do projeto legado — precisa ser revogado.
4. **4 GB é o teto estrutural.** Postiz (com Temporal e Elasticsearch) e n8n sozinhos
   ocupam ~900 MB. O Seller Radar cabe, mas sem folga para picos.
5. **Dois *proxy hosts* órfãos** no proxy reverso, apontando para containers removidos
   (retornam 502).

## Dimensionamento previsto para o Seller Radar

| Recurso | Estimativa |
|---|---|
| RAM | ~600–800 MB em regime (com limites por container) |
| Disco | ~1,2 GB (imagens + banco + backups) |
| CPU | picos curtos na sincronização diária |

Caberá no espaço liberado, com folga apertada. Se o iowait permanecer acima de 10% com a
máquina ociosa, o caminho é ampliar a VPS — decisão adiada, não descartada.
