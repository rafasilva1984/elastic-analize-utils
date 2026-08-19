#!/usr/bin/env bash
# 05-templates-and-pipelines.sh
# [GIT BASH - READ-ONLY]
# Coleta index templates (legacy e composable), component templates e
# ingest pipelines.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${DIR}/lib/common.sh"

check_dependencies || exit 1

es_get "/_index_template"                                                  "05-index-templates.json"
es_get "/_component_template"                                              "05-component-templates.json"
es_get "/_template"                                                         "05-legacy-templates.json" || \
  log "WARN" "Templates legados (/_template) podem estar vazios/depreciados; nao critico."
es_get "/_ingest/pipeline"                                                  "05-ingest-pipelines.json"
es_get "/_ingest/geoip/stats"                                               "05-geoip-stats.json" || \
  log "WARN" "geoip/stats pode nao existir; nao critico."

log "INFO" "05-templates-and-pipelines.sh concluido."
