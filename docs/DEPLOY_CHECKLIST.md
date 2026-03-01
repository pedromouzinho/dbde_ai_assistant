# DBDE AI Assistant — Checklist de Deploy
## Versao: 7.3.0

## Pre-Deploy
- [ ] Todos os testes passam localmente (`python -m pytest tests/ -x`)
- [ ] Frontend build sem erros (`npm run build`)
- [ ] Sem secrets hardcoded no codigo
- [ ] Mudancas documentadas em commit/PR
- [ ] Branch atualizada com main
- [ ] PR aprovado (quando aplicavel)

## Deploy para Staging
- [ ] Deploy no slot staging
- [ ] Aguardar startup completo
- [ ] Smoke test no staging (`python scripts/smoke_test.py <staging_url>`)
- [ ] Verificar `/health?deep=true` no staging
- [ ] Verificar logs no Log Stream

## Swap Staging → Production
- [ ] Smoke test staging PASSED
- [ ] Executar `bash scripts/deploy_swap.sh`
- [ ] Smoke test production PASSED
- [ ] Workers vivos no deep health
- [ ] Monitorizar erros 5 min

## Pos-Deploy
- [ ] Testar chat basico e upload
- [ ] Confirmar segredos/PAT validos
- [ ] Confirmar alertas Azure Monitor
- [ ] Atualizar plano/status de release

## Rollback
- [ ] Executar `bash scripts/rollback.sh`
- [ ] Verificar health em production
- [ ] Registar razao de rollback
- [ ] Abrir task de correcao
