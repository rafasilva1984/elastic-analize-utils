#!/usr/bin/env bash
# 09-kibana-alerting.sh
# [GIT BASH - READ-ONLY]
# Coleta regras de alerta do Kibana (Alerting Framework), conectores e,
# quando disponivel, um resumo de execucoes recentes via _find. NAO
# habilita, desabilita, silencia, testa conector nem executa regra.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${DIR}/lib/common.sh"

check_dependencies || exit 1

PER_PAGE=100
PAGE=1
BASE_OUT="09-kibana-alerting-rules-page"

while :; do
  outfile="${BASE_OUT}${PAGE}.json"
  kb_get "/api/alerting/rules/_find?per_page=${PER_PAGE}&page=${PAGE}" "${outfile}" || break
  total="$(jq -r '.total // 0' "${EVIDENCE_DIR}/${outfile}" 2>/dev/null || echo 0)"
  got="$(jq -r '.data | length' "${EVIDENCE_DIR}/${outfile}" 2>/dev/null || echo 0)"
  log "INFO" "Pagina ${PAGE}: ${got} regras (total reportado: ${total})"
  if [[ "${got}" -lt "${PER_PAGE}" || "${got}" -eq 0 ]]; then
    break
  fi
  PAGE=$((PAGE + 1))
  if [[ "${PAGE}" -gt 50 ]]; then
    log "WARN" "Interrompendo paginacao de regras apos 50 paginas (5000 regras) por seguranca."
    break
  fi
done

kb_get "/api/actions/connectors"                                              "09-kibana-connectors.json"
kb_get "/api/alerting/rule_types"                                              "09-kibana-rule-types.json"

log "INFO" "09-kibana-alerting.sh concluido. Nenhuma regra foi criada, editada, habilitada, desabilitada, silenciada ou executada."
