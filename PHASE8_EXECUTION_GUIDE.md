# PHASE 8 Execution Guide (Model + Retrieval + Export)

## Objective
Stabilize quality/cost routing and retrieval accuracy before export infra hardening.

## Ordered Plan
1. Tier switch
- Standard: `azure_openai:gpt-5-mini`
- Pro: `azure_openai:gpt-5-pro`
- Keep `fast` as operational safety tier.

2. Model Router in test only
- Enable with `MODEL_ROUTER_ENABLED=true` in test.
- Keep `MODEL_ROUTER_NON_PROD_ONLY=true` until validation complete.
- Target tiers default: `standard,pro`.

3. Post-retrieval rerank
- Enable with `RERANK_ENABLED=true`.
- Use `RERANK_MODEL=cohere-rerank-v4.0-fast`.
- Configure `RERANK_ENDPOINT` and auth (`RERANK_AUTH_MODE`, `RERANK_API_KEY`).
- Apply only as reorder layer; never fail user request if rerank fails.

4. Export infra (after 1-3 are stable)
- Add bundle zip endpoint (CSV+XLSX+PDF in one click).
- Move heavy export generation to dedicated async worker.

## Validation Gates
- Run preflight before any rollout:
  - `python3 scripts/deploy/preflight_architecture_check.py --resource-group <rg> --webapp <app> --aoai-resource-group <aoai-rg> --aoai-account <aoai-account>`
- `/health` = 200
- `/api/info` shows expected tiers + router/rerank status.
- Query hierarchy and search tools return consistent counts and links.
- No regression in export buttons (`CSV`, `XLSX`, `PDF`, `HTML`).

## Rollback
- Disable router: `MODEL_ROUTER_ENABLED=false`
- Disable rerank: `RERANK_ENABLED=false`
- Revert tiers via env vars (`LLM_TIER_STANDARD`, `LLM_TIER_PRO`) without code rollback.
