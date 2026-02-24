# Deploy v7.2.1 (Immutable + Runtime Verify)

## Objetivo
Fechar Fase A e suportar Fases B/C com verificação pós-deploy e worker de upload desacoplado.

## Pré-requisitos
- `jq`
- token admin da app para chamar `/api/runtime/check`
- credenciais Kudu para zipdeploy

## 1) Build manifest local
```bash
./scripts/deploy/build_runtime_manifest.sh ./deploy/runtime-manifest.json
```

## 2) Deploy imutável + verificação runtime
```bash
./scripts/deploy/immutable_zip_deploy.sh \
  "https://<app>.scm.azurewebsites.net/api/zipdeploy" \
  "<KUDU_BASIC_AUTH_BASE64>" \
  "https://dbdeai.pt/api/runtime/check" \
  "<BEARER_ADMIN_TOKEN>"
```

## 3) Worker mode (Fase C)

### Opção A (compatível)
- `UPLOAD_INLINE_WORKER_ENABLED=true`
- O web app processa fila localmente por polling.

### Opção B (recomendada para desacoplamento)
- Web app: `UPLOAD_INLINE_WORKER_ENABLED=false`
- Worker dedicado: executar `upload_worker.py`

Exemplo local:
```bash
python upload_worker.py --batch-size 4 --poll-seconds 2.5
```

Exemplo startup script worker:
```bash
./startup_worker.sh
```

## 4) Variáveis novas
- `UPLOAD_BLOB_CONTAINER_RAW` (default `upload-raw`)
- `UPLOAD_BLOB_CONTAINER_TEXT` (default `upload-text`)
- `UPLOAD_BLOB_CONTAINER_CHUNKS` (default `upload-chunks`)
- `UPLOAD_INDEX_TOP` (default `200`)
- `UPLOAD_INLINE_WORKER_ENABLED` (default `true`)
- `UPLOAD_WORKER_POLL_SECONDS` (default `2.5`)
- `UPLOAD_WORKER_BATCH_SIZE` (default `4`)

## 5) Endpoints novos/relevantes
- `POST /api/upload/worker/run-once` (admin)
- `GET /api/upload/index/{conversation_id}`
- `GET /api/runtime/check` (agora com `manifest_check`)

## Notas
- `/upload` passa a enfileirar e devolver `job_id` (sem processamento síncrono pesado).
- `search_uploaded_document` usa `UploadIndex` + blobs de chunks; fallback memória apenas para retrocompatibilidade.
