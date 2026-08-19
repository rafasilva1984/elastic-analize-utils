#!/usr/bin/env bash
# 10-kibana-saved-objects.sh
# [GIT BASH - READ-ONLY]
# Coleta data views, dashboards e visualizations via api/saved_objects/_find
# (GET, read-only). Paginado para evitar respostas excessivamente grandes.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${DIR}/lib/common.sh"

check_dependencies || exit 1

fetch_type() {
  local type="$1"
  local label="$2"
  local per_page=100
  local page=1
  while :; do
    local outfile="10-so-${label}-page${page}.json"
    kb_get "/api/saved_objects/_find?type=${type}&per_page=${per_page}&page=${page}" "${outfile}" || break
    local got total
    got="$(jq -r '.saved_objects | length' "${EVIDENCE_DIR}/${outfile}" 2>/dev/null || echo 0)"
    total="$(jq -r '.total // 0' "${EVIDENCE_DIR}/${outfile}" 2>/dev/null || echo 0)"
    log "INFO" "${label} pagina ${page}: ${got} objetos (total reportado: ${total})"
    if [[ "${got}" -lt "${per_page}" || "${got}" -eq 0 ]]; then
      break
    fi
    page=$((page + 1))
    if [[ "${page}" -gt 50 ]]; then
      log "WARN" "Interrompendo paginacao de '${label}' apos 50 paginas por seguranca."
      break
    fi
  done
}

fetch_type "index-pattern" "dataviews"
fetch_type "dashboard"     "dashboards"
fetch_type "visualization" "visualizations"
fetch_type "lens"          "lens"
fetch_type "search"        "saved-searches"
fetch_type "map"           "maps"

log "INFO" "10-kibana-saved-objects.sh concluido."
