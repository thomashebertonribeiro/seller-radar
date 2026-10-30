"""Sincronização somente leitura do Mercado Livre.

`engine` orquestra (testável com fonte falsa), `source` fala com a API,
`store` grava, `parse` traduz, `freight` rateia e `runner` junta tudo com lock.
"""
