#!/usr/bin/env bash
# 04-ilm.sh
# [GIT BASH - READ-ONLY]
# Coleta politicas ILM e o status ILM explain para todos os indices
# (identifica indices presos/errored no ILM).
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${DIR}/lib/common.sh"

check_dependencies || exit 1

es_get "/_ilm/policy"                                                     "04-ilm-policies.json"
es_get "/_ilm/status"                                                      "04-ilm-status.json"
es_get "/_all/_ilm/explain?only_errors=false&only_managed=true"            "04-ilm-explain-managed.json"
es_get "/_all/_ilm/explain?only_errors=true"                                "04-ilm-explain-errors.json"
es_get "/_slm/policy"                                                       "04-slm-policies.json"
es_get "/_slm/stats"                                                        "04-slm-stats.json"

log "INFO" "04-ilm.sh concluido."
