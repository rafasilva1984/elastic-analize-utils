#!/usr/bin/env bash
# 01-cluster-overview.sh
# [GIT BASH - READ-ONLY]
# Coleta: versao, saude do cluster, licenca, settings persistentes/transientes,
# distribuicao de nos por tier. Todas as chamadas sao GET read-only.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${DIR}/lib/common.sh"

check_dependencies || exit 1

es_get "/"                                                    "01-es-root.json"
es_get "/_cluster/health?level=indices"                       "01-cluster-health.json"
es_get "/_cluster/health?level=shards"                        "01-cluster-health-shards.json"
es_get "/_license"                                             "01-license.json"
es_get "/_xpack"                                                "01-xpack-features.json"
es_get "/_cluster/settings?include_defaults=true&flat_settings=true" "01-cluster-settings.json"
es_get "/_cluster/stats"                                        "01-cluster-stats.json"
es_get "/_cat/master?v&format=json"                             "01-cat-master.json"
es_get "/_cat/health?v&format=json"                             "01-cat-health.json"
es_get "/_cat/nodeattrs?v&format=json"                          "01-cat-nodeattrs.json"
es_get "/_cat/plugins?v&format=json"                            "01-cat-plugins.json"

kb_get "/api/status"                                            "01-kibana-status.json"

log "INFO" "01-cluster-overview.sh concluido."
