# Segurança

## Regras do repositório

- **Nenhum segredo versionado.** Apenas `.env.example`, com nomes de variáveis e sem valores.
- Tokens, chaves e credenciais vivem em `.env` na máquina de destino, fora do Git.
- Tokens OAuth do Mercado Livre são gravados **criptografados** no banco; a chave de
  criptografia vem do ambiente.
- Logs não registram tokens, segredos nem dados pessoais de compradores.

## Se encontrar um segredo exposto

1. **Revogar** a credencial no serviço de origem (não apenas remover do código).
2. Trocar por uma nova, inserida direto no `.env` do servidor.
3. Verificar histórico do Git — remover do commit atual não remove do histórico.

## Escopo de acesso ao Mercado Livre

O aplicativo opera em **modo somente leitura**:

- Somente requisições `GET` contra a API.
- Exceção única: `POST /oauth/token`, para troca de código e renovação.
- Nenhuma ação comercial ou financeira é executada, nem por sugestão da IA.

## Dados pessoais (LGPD)

- Guardar do comprador apenas o necessário para análise.
- Não armazenar endereço, documento ou telefone sem justificativa registrada.
- Dados enviados à Claude API: apenas agregados e indicadores — nunca dados de compradores,
  tokens ou segredos.

## Reportar problema

Este é um projeto pessoal, sem programa formal de recompensas. Encontrou algo?
Abra uma issue privada ou entre em contato diretamente com o autor.
