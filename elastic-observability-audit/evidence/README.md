# evidence

Saída bruta (JSON) dos scripts em `../scripts/read-only-audit/`. Gerada
localmente ao executar os scripts com as variáveis de ambiente configuradas
(ver `../README.md`).

**Importante:**
- Este diretório é o destino padrão de `EVIDENCE_DIR` em
  `scripts/read-only-audit/lib/common.sh`.
- **Este diretório fica fora do controle de versão por padrão** (ver
  `.gitignore` na raiz do repositório) — é dado bruto de um cluster
  específico, tipicamente de produção de um cliente, e não deve ser
  publicado. Se por algum motivo excepcional precisar versionar algo daqui
  (ex.: um repositório privado interno do próprio time dono do cluster),
  revise manualmente por credenciais, tokens, cookies, PII ou URLs internas
  sensíveis antes de qualquer `git add` — os scripts não devem capturá-los,
  mas a revisão humana é obrigatória (ver nota em
  `lib/common.sh::sanitize_note`).
- Vazio até que `audit_collect.py`/`run-all.sh` seja executado localmente
  contra um cluster real.
