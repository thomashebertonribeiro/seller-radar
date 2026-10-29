# Fórmulas financeiras

Todo cálculo é feito por código determinístico e testado. A IA **interpreta** resultados; ela
nunca calcula nem inventa números.

Convenções:

- Valores monetários em `Decimal` / `NUMERIC`, nunca `float`.
- Datas armazenadas em UTC, cortes de período no fuso `America/Sao_Paulo`.
- Pedido entra na receita pela **data de pagamento/fechamento**.
- Cancelados ficam fora da receita e são exibidos à parte.
- Devoluções e reembolsos são abatidos **no período em que ocorrem**.

---

## 1. Receita bruta

```
receita_bruta = Σ (preço_unitário × quantidade) − desconto_do_vendedor
```

**Desconto do vendedor** é o que sai do seu bolso: reduz a receita.
**Subsídio do Mercado Livre** (cupom/campanha bancada pela plataforma) **não** é descontado —
ele não reduz o que você recebe. Somar os dois como "desconto" subestima a receita e foi
descartado.

**Exemplo:** 2 unidades a R$ 100,00 com R$ 10,00 de desconto concedido pelo vendedor.

```
2 × 100,00 − 10,00 = 190,00
```

## 2. Valor líquido estimado do Mercado Livre

```
valor_líquido = receita_bruta
              − tarifa_percentual
              − tarifa_fixa_por_unidade × quantidade
              − frete_pago_pelo_vendedor
```

A tarifa do Mercado Livre tem **duas partes**: percentual sobre o preço e, em faixas de preço
baixo, um valor fixo por unidade. A versão anterior deste documento tratava tudo como
percentual, o que subestima o custo em itens baratos.

**Exemplo:** tarifa de 12% sobre a receita bruta, sem tarifa fixa, frete de R$ 15,00 por conta
do vendedor.

```
tarifa = 190,00 × 0,12 = 22,80
valor_líquido = 190,00 − 22,80 − 15,00 = 152,20
```

> Este valor **não é lucro**. É o que sobra antes de custo de produto, imposto e despesas
> variáveis.

## 3. Margem de contribuição

```
mc = valor_líquido
     − custo_do_produto_vigente_na_data_da_venda
     − impostos
     − embalagem_e_outros_variáveis
     − publicidade_atribuída
```

```
impostos = receita_bruta × alíquota
```

**Exemplo:** custo de R$ 40,00/unidade (2 un.), alíquota de 6%, embalagem R$ 2,00,
publicidade atribuída R$ 5,00.

```
impostos = 190,00 × 0,06 = 11,40
custo    = 40,00 × 2 = 80,00
mc = 152,20 − 80,00 − 11,40 − 2,00 − 5,00 = 53,80
mc% = 53,80 ÷ 190,00 = 28,3158%
```

O custo usado é sempre o **vigente na data da venda** (tabela de custos com data inicial e
final), não o custo atual do SKU.

## 4. Ponto de equilíbrio

```
mc_percentual_ponderada = Σ mc ÷ Σ receita_bruta
ponto_de_equilíbrio     = custos_fixos_mensais ÷ mc_percentual_ponderada
```

A ponderação é **por receita**, não média simples entre anúncios. Média simples dá peso igual a
um item que vende R$ 50 e a outro que vende R$ 9.000, e produz um ponto de equilíbrio errado.

**Exemplo da diferença:**

| Anúncio | Receita | MC | MC% |
|---|---|---|---|
| A | 9.000,00 | 2.700,00 | 30% |
| B | 1.000,00 | 200,00 | 20% |

```
ponderada = 2.900,00 ÷ 10.000,00 = 29,00%  → 5.000,00 ÷ 0,29 = 17.241,38
média simples = (30% + 20%) ÷ 2 = 25,00%   → 5.000,00 ÷ 0,25 = 20.000,00
```

A diferença é de R$ 2.758,62 — 16% de erro se a média for simples.

Só é calculado quando existem custos fixos cadastrados **e** receita no período. Sem eles, o
painel mostra o motivo, nunca `0`.

## 5. Preço mínimo para margem-alvo

```
preço_mínimo = (custo + frete + outros_variáveis + tarifa_fixa)
               ÷ (1 − tarifa_percentual − imposto − margem_alvo)
```

Tarifa percentual e imposto incidem sobre o preço; frete, custo, outros e tarifa fixa são
absolutos — por isso a tarifa fixa entra no numerador, e não no denominador.

**Exemplo sem tarifa fixa:** custo 40,00 · frete 15,00 · outros 2,00 · tarifa 12% · imposto 6% ·
alvo 20%.

```
(40,00 + 15,00 + 2,00) ÷ (1 − 0,12 − 0,06 − 0,20)
= 57,00 ÷ 0,62
= 91,9355 → 91,94
```

**Exemplo com tarifa fixa de R$ 6,00 por unidade:**

```
(40,00 + 15,00 + 2,00 + 6,00) ÷ 0,62 = 63,00 ÷ 0,62 = 101,6129 → 101,62
```

Conferência do segundo caso:

```
tarifa %  = 101,62 × 0,12 = 12,19
tarifa fixa                =  6,00
imposto   = 101,62 × 0,06 =  6,10
líquido   = 101,62 − 12,19 − 6,00 − 15,00 = 68,43
mc        = 68,43 − 40,00 − 6,10 − 2,00 = 20,33
mc%       = 20,33 ÷ 101,62 = 20,01%  ✓
```

**Arredondamento para cima, sempre.** O preço mínimo é arredondado com `ROUND_CEILING` para
dois decimais. Arredondar para baixo entregaria um preço que **não** atinge a margem-alvo — o
exemplo acima cairia para 19,999% com `91,93`.

**Limitações conhecidas:**

- Quando o frete varia por faixa de preço, a equação deixa de ser linear. O cálculo itera por
  faixa (resolve, verifica a faixa resultante e recalcula) e o resultado é marcado como
  `estimado`.
- Quando `1 − tarifa − imposto − margem_alvo ≤ 0`, o alvo é inatingível em qualquer preço. O
  painel informa isso; não devolve número.

## 6. Conversão

```
conversão = vendas ÷ visitas
```

Calculada **apenas** quando visitas e vendas cobrem o mesmo período e o mesmo anúncio.
Fora disso, o campo aparece como indisponível — nunca como zero.

## 7. Publicidade atribuída

Enquanto o endpoint de Product Ads estiver `pendente`, a publicidade é **informada
manualmente** por período e rateada entre os anúncios proporcionalmente à receita de cada um.

Enquanto não houver dado de publicidade no período, `publicidade_atribuída = 0` e o resultado
é marcado **`incompleto`** com o motivo `publicidade`. Zero por ausência de dado não é o mesmo
que zero por não haver gasto.

## 8. Arredondamento

| Situação | Regra |
|---|---|
| Valores monetários exibidos ou persistidos | `ROUND_HALF_UP`, 2 casas |
| Preço mínimo | `ROUND_CEILING`, 2 casas |
| Percentuais exibidos | `ROUND_HALF_UP`, 4 casas |
| Intermediários | **sem** quantização (precisão cheia do `Decimal`) |

**Agregados são somados antes de arredondar.** Somar 100 valores já arredondados acumula até
R$ 0,50 de erro; arredondar uma vez no total, não.

Divisão por zero nunca vira `0`: vira indisponível, com o motivo.

## 9. Estados de qualidade

| Estado | Significado |
|---|---|
| `completo` | todos os componentes disponíveis e conferidos |
| `estimado` | algum componente é aproximação (ex.: frete rateado, faixa de preço) |
| `incompleto` | componente ausente; o painel informa **qual** (ex.: SKU sem custo) |

O estado carrega a lista de componentes faltantes, não só o rótulo. Uma margem calculada sobre
SKU sem custo cadastrado é `incompleto` com motivo `custo`, não zero.

## 10. Premissas fiscais

`TAX_RATE` é uma **alíquota efetiva única** sobre a receita bruta. O modelo **não** separa
ICMS/DIFAL, PIS/COFINS, ISS ou Simples por faixa de faturamento. É uma simplificação declarada:
serve para comparação entre anúncios, não para apuração fiscal.

Se a alíquota efetiva mudar dentro do período (mudança de faixa do Simples, por exemplo), o
relatório precisa ser gerado com a alíquota do período — não com uma média do ano.
