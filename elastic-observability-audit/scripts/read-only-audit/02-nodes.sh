#!/usr/bin/env bash
# 02-nodes.sh
# [GIT BASH - READ-ONLY]
# Coleta info/stats de nos: heap, GC, CPU, disco, thread pools, filas,
# rejeicoes, circuit breakers, indexing pressure.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${DIR}/lib/common.sh"

check_dependencies || exit 1

es_get "/_nodes"                                                       "02-nodes-info.json"
es_get "/_nodes/stats"                                                  "02-nodes-stats.json"
es_get "/_nodes/stats/breaker"                                          "02-nodes-breakers.json"
es_get "/_nodes/stats/thread_pool"                                      "02-nodes-threadpool.json"
es_get "/_nodes/stats/indices/indexing_pressure"                        "02-nodes-indexing-pressure.json" || \
  log "WARN" "indexing_pressure pode nao existir nesta versao/licenca - erro registrado, nao e critico."
es_get "/_cat/nodes?v&format=json&h=name,node.role,heap.percent,heap.max,ram.percent,cpu,load_1m,load_5m,load_15m,disk.used_percent,disk.avail,master" \
                                                                          "02-cat-nodes.json"
es_get "/_cat/thread_pool?v&format=json&h=node_name,name,active,queue,rejected,size" "02-cat-threadpool.json"
es_get "/_cat/pending_tasks?v&format=json"                               "02-cat-pending-tasks.json"
es_get "/_cat/recovery?v&format=json&active_only=true"                   "02-cat-recovery-active.json"
es_get "/_tasks?detailed=false"                                          "02-tasks-inflight.json"

log "INFO" "02-nodes.sh concluido."
