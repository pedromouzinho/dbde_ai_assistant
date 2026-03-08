# Email Tools

Esta capability cobre dois fluxos principais no agente:

1. `prepare_outlook_draft`
   - Objetivo: transformar um email já aprovado num pack pronto para Outlook.
   - Output:
     - `Open_<nome>.ps1`
     - `<nome>.eml`
     - `<nome>.draft.json`

2. `classify_uploaded_emails`
   - Objetivo: analisar CSV/XLSX de emails carregado na conversa, aplicar critérios dados no momento e devolver um pack consumível no Outlook.
   - Output:
     - `Apply_<nome>.ps1`
     - `<nome>.xlsx`
     - `<nome>.csv`
     - `<nome>.json`

## Prompts úteis

### Draft Outlook

- `Escreve-me um email para o cliente a explicar o atraso da entrega.`
- `Mantém tom formal e curto.`
- `Está bom. Faz-me o rascunho Outlook para joao@empresa.pt com cc para maria@empresa.pt.`

### Triagem inbox

- `Analisa este CSV de emails não lidos e marca como urgentes os que tenham bloqueios operacionais, fraude, password reset ou pedidos com SLA para hoje.`
- `Usa estas labels: Urgente -> flag today; Revisão -> category AI-Review; FYI -> none.`
- `Quero um ficheiro pronto para o Outlook mover por pasta: MCCA, Sucursal, Non Site e App, Screenshots.`

## Formato esperado do ficheiro de emails

O classificador aceita:

- CSV/XLSX tabular com colunas como `EntryID`, `Subject`, `From`, `Body`, `SenderName`, `ReceivedTime`, `Importance`, `Attachments`
- O formato antigo do Agent Lab com coluna única `MessageInput`

## Notas de produto

- O browser não consegue executar Outlook diretamente por razões de segurança do lado do cliente.
- O fluxo mais direto é:
  - no draft: clicar em `Abrir draft no Outlook (.ps1)`
  - na triagem: clicar em `Aplicar ações no Outlook (.ps1)`
- Os botões do chat usam labels específicas para evitar que o utilizador tenha de adivinhar qual download interessa.
