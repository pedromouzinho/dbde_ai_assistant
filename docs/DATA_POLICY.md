# DBDE AI Assistant — Politica de Dados
## Versao: 7.3.0

## 1. Ambito
Aplica-se ao uso interno do DBDE AI Assistant.

## 2. Dados Permitidos
- Work items de engenharia/produto
- CSV/Excel sem PII de cliente
- Documentacao interna tecnica
- Screenshots de UI para user stories

## 3. Dados Proibidos
- Dados pessoais de clientes (conta, NIF, morada, etc.)
- Credenciais (passwords, tokens, keys)
- Dados regulados/sensiveis fora de politica
- Documentos classificados acima do nivel permitido

## 4. Fluxos Externos
Ver [THIRD_PARTY_INVENTORY.md](./THIRD_PARTY_INVENTORY.md).

## 5. Retencao
- Conversas e anexos: conforme politica interna e necessidade operacional
- Exports: retencao limitada
- Logs: retencao para auditoria operacional

## 6. Controles
- JWT auth, roles user/admin
- Rate limiting e quotas
- Logging estruturado
- Revisao periodica de acessos e segredos

## 7. Responsabilidades
- PO: aprova excecoes/politica
- Utilizadores: nao inserir dados proibidos
- Operacao: garantir controls e monitorizacao
