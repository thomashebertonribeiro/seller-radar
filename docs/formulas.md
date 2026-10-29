# Fórmulas financeiras

Todo cálculo é feito por código determinístico e testado. A IA **interpreta** resultados;
ela nunca calcula nem inventa números.

Convenções:

- Valores monetários em `Decimal` / `NUMERIC`, nunca `float`.
- Datas armazenadas em UTC, cortes de período no fuso `America/Sao_Paulo`.
- Pedido entra na receita pela **data de pagamento/fechamento**.
- Cancelados ficam fora da receita e são exibidos à parte.
- Devoluções e reembolsos são abatidos **no período em que ocorrem**.

---

## 1. Receita bruta

```
receita_bruta = Σ (preço_unitário × quantidade) − descontos_do_vendedor
```

**Exemplo:** 2 unidades a R$ 100,00 com R$ 10,00 de desconto concedido pelo vendedor.

```
2 × 100,00 − 10,00 = 190,00
```

## 2. Valor líquido estimado do Mercado Livre

```
valor_líquido = receita_bruta − tarifa_de_venda − frete_pago_pelo_vendedor
```

**Exemplo:** tarifa de 12% sobre a receita bruta e frete de R$ 15,00 por conta do vendedor.

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
mc% = 53,80 ÷ 190,00 = 28,32%
```

O custo usado é sempre o **vigente na data da venda** (tabela de custos com data inicial e
final), não o custo atual do SKU.

## 4. Ponto de equilíbrio

```
ponto_de_equilíbrio = custos_fixos_mensais ÷ mc_percentual_média
```

Só é calculado quando existem custos fixos cadastrados. Sem eles, o painel mostra
"não calculável — custos fixos não cadastrados".

**Exemplo:** custos fixos de R$ 5.000,00 e margem média de 28,3158%.

```
5.000,00 ÷ 0,283158 = 17.657,99
```

Ou seja: ~R$ 17.658,00 de receita bruta no mês para cobrir a estrutura.

## 5. Preço mínimo para margem-alvo

Tarifa e imposto são percentuais sobre o preço; frete e custo são absolutos.

```
preço_mínimo = (custo + frete + outros_variáveis) ÷ (1 − tarifa% − imposto% − margem_alvo)
```

**Exemplo:** custo 40,00 · frete 15,00 · outros 2,00 · tarifa 12% · imposto 6% · alvo 20%.

```
(40,00 + 15,00 + 2,00) ÷ (1 − 0,12 − 0,06 − 0,20)
= 57,00 ÷ 0,62
= 91,94
```

Conferência:

```
tarifa  = 91,94 × 0,12 = 11,03
imposto = 91,94 × 0,06 =  5,52
líquido = 91,94 − 11,03 − 15,00 = 65,91
mc      = 65,91 − 40,00 − 5,52 − 2,00 = 18,39
mc%     = 18,39 ÷ 91,94 = 20,00%  ✓
```

**Limitação conhecida:** quando o frete varia por faixa de preço, a equação deixa de ser
linear. Nesse caso o cálculo itera por faixa (resolve, verifica a faixa resultante e
recalcula) e o resultado é marcado como `estimado`.

## 6. Conversão

```
conversão = vendas ÷ visitas
```

Calculada **apenas** quando visitas e vendas cobrem o mesmo período e o mesmo anúncio.
Fora disso, o campo aparece como indisponível — nunca como zero.

## 7. Estados de qualidade

| Estado | Significado |
|---|---|
| `completo` | todos os componentes disponíveis e conferidos |
| `estimado` | algum componente é aproximação (ex.: frete rateado, faixa de preço) |
| `incompleto` | componente ausente; o painel informa **qual** (ex.: SKU sem custo) |

Uma margem calculada sobre SKU sem custo cadastrado é `incompleto`, não zero.
