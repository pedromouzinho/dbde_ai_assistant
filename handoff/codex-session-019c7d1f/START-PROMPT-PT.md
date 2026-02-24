Continua a implementacao do upgrade do DBDE AI Assistant com base no seguinte estado validado:

- Fase A: parcial
  - Existe runtime check de hash/markers.
  - Falta pipeline de deploy imutavel + verificacao automatica pos-deploy.
  - VFS ainda existe fora de modo estritamente emergencia.

- Fase B: maioritariamente pendente
  - Nao existe persistencia de anexos em Blob Storage.
  - Nao existe tabela UploadIndex persistida por conversa/ficheiro.
  - search_uploaded_document ainda le de memoria local (uploaded_files_store).
  - embeddings/chunks ainda estao dependentes de memoria de instancia.

- Fase C: avancada mas parcial
  - Existem endpoints: /upload/async, /upload/batch/async, /api/upload/status/{job_id}, /api/upload/status/batch, /api/upload/pending/{conversation_id}.
  - Jobs em UploadJobs table existem.
  - Processamento ainda em asyncio.create_task dentro da web app (sem worker externo dedicado).
  - /upload legacy sincronico ainda existe.

- Fase D: nao iniciada
  - ainda nao escalar horizontalmente antes de fechar B/C.

Objetivo imediato:
1) Fechar Fase A (pipeline imutavel + verificacao automatica).
2) Implementar Fase B completa (Blob + UploadIndex + query por indice persistido).
3) Endurecer Fase C para worker/background desacoplado da web app.
4) So depois preparar Fase D (scale out 2+).

Repo:
- /Users/pedromousinho/Downloads/dbde-ai-v7-patched

Antes de alterar codigo:
- Validar estado real atual no codigo e endpoints.
- Nao assumir logs antigos.
- Produzir checklist de gaps e depois implementar.
