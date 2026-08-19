#!/usr/bin/env bash
# 07-ml-and-transforms.sh
# [GIT BASH - READ-ONLY]
# Coleta jobs de Machine Learning, datafeeds e transforms (definicao e stats).
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${DIR}/lib/common.sh"

check_dependencies || exit 1

es_get "/_ml/anomaly_detectors"                                              "07-ml-jobs.json"
es_get "/_ml/anomaly_detectors/_stats"                                        "07-ml-jobs-stats.json"
es_get "/_ml/datafeeds"                                                        "07-ml-datafeeds.json"
es_get "/_ml/datafeeds/_stats"                                                  "07-ml-datafeeds-stats.json"
es_get "/_ml/trained_models"                                                    "07-ml-trained-models.json" || \
  log "WARN" "trained_models pode nao existir; nao critico."
es_get "/_transform"                                                             "07-transforms.json"
es_get "/_transform/_stats"                                                       "07-transforms-stats.json"

log "INFO" "07-ml-and-transforms.sh concluido."
