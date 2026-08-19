#!/usr/bin/env bash
# 08-watcher.sh
# [GIT BASH - READ-ONLY]
# Coleta stats do Watcher e busca watches via a API de query read-only
# (_watcher/_query/watches). NAO executa watches (_execute) e NAO altera
# estado de nenhum watch (_activate/_deactivate/_ack).
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${DIR}/lib/common.sh"

check_dependencies || exit 1

es_get "/_watcher/stats?metric=all"                                               "08-watcher-stats.json"

# _watcher/_query/watches e um endpoint de busca (POST semanticamente read-only,
# similar a _search). Usamos um corpo minimo para paginar ate 1000 watches.
QUERY_FILE="$(mktemp)"
cat > "${QUERY_FILE}" <<'EOF'
{
  "size": 1000
}
EOF

base="$(resolve_es_url)" || exit 1
auth="$(build_auth_header)" || { log "ERROR" "Credencial invalida."; exit 1; }
http_code="$(curl -sS "${CURL_INSECURE_FLAG[@]}" -o "${EVIDENCE_DIR}/08-watches-query.json" -w '%{http_code}' \
  -H "${auth}" -H 'Content-Type: application/json' \
  -X POST "${base}/_watcher/_query/watches" --data-binary "@${QUERY_FILE}")"
rm -f "${QUERY_FILE}"

if [[ "${http_code}" -ge 200 && "${http_code}" -lt 300 ]]; then
  log "INFO" "POST /_watcher/_query/watches -> HTTP ${http_code} salvo em evidence/08-watches-query.json"
else
  log "WARN" "POST /_watcher/_query/watches -> HTTP ${http_code} (endpoint pode nao existir nesta versao; Watcher pode estar desabilitado). Ver evidence/08-watches-query.json"
fi

log "INFO" "08-watcher.sh concluido. NENHUM watch foi executado, ativado, desativado ou reconhecido."
