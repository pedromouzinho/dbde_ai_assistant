# Handoff Codex (outra conta no mesmo computador)

Sessao-alvo principal:
- session_id: `019c7d1f-400d-7e40-a12a-c44886704897`
- ficheiro: `rollout-2026-02-20T22-15-25-019c7d1f-400d-7e40-a12a-c44886704897.jsonl`

## Opcao A (preferida): retomar direto
No terminal, no projeto:

```bash
cd /Users/pedromousinho/Downloads/dbde-ai-v7-patched
codex resume 019c7d1f-400d-7e40-a12a-c44886704897 -C /Users/pedromousinho/Downloads/dbde-ai-v7-patched
```

## Opcao B (se a nova conta nao conseguir ver a sessao)
1. Garante que o ficheiro esta em:
`~/.codex/sessions/2026/02/20/rollout-2026-02-20T22-15-25-019c7d1f-400d-7e40-a12a-c44886704897.jsonl`

2. Correr:

```bash
codex resume 019c7d1f-400d-7e40-a12a-c44886704897 -C /Users/pedromousinho/Downloads/dbde-ai-v7-patched
```

## Opcao C (fallback universal)
Abrir novo chat e colar o ficheiro `START-PROMPT-PT.md`.

