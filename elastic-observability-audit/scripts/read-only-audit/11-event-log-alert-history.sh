#!/usr/bin/env bash
# 11-event-log-alert-history.sh
# [GIT BASH - READ-ONLY]
# Consulta agregada (size:0, sem baixar documentos individuais) contra
# .kibana-event-log-* para obter historico de execucao de alertas dos
# ultimos 90 dias (disparos, recuperacoes, falhas, duracao media, por
# rule.id). Fecha a lacuna registrada em blind-spots.md item 2.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${DIR}/lib/common.sh"

check_dependencies || exit 1

QUERY_FILE="${AUDIT_ROOT}/queries/read-only-audit/event-log-alert-history-90d.json"
if [[ ! -f "${QUERY_FILE}" ]]; then
  log "ERROR" "Arquivo de consulta nao encontrado: ${QUERY_FILE}"
  exit 1
fi

es_search_post "/.kibana-event-log-*/_search" "${QUERY_FILE}" "11-event-log-alert-history-90d.json" || \
  log "WARN" "Busca em .kibana-event-log-* falhou (indice pode nao existir ainda, ou nao ha eventos no periodo)."

log "INFO" "11-event-log-alert-history.sh concluido."
