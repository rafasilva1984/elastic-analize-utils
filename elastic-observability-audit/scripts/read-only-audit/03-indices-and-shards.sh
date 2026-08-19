#!/usr/bin/env bash
# 03-indices-and-shards.sh
# [GIT BASH - READ-ONLY]
# Inventario de indices, aliases, shards, data streams. Usa _cat com
# colunas explicitas para evitar respostas excessivamente grandes.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${DIR}/lib/common.sh"

check_dependencies || exit 1

es_get "/_cat/indices?v&format=json&h=index,health,status,pri,rep,docs.count,docs.deleted,store.size,pri.store.size,creation.date.string" \
                                                                          "03-cat-indices.json"
es_get "/_cat/aliases?v&format=json"                                     "03-cat-aliases.json"
es_get "/_cat/shards?v&format=json&h=index,shard,prirep,state,docs,store,node,unassigned.reason" \
                                                                          "03-cat-shards.json"
es_get "/_cat/shards?v&format=json&h=index,shard,prirep,state,unassigned.reason&s=state" \
                                                                          "03-cat-shards-by-state.json"
es_get "/_data_stream"                                                    "03-data-streams.json"
es_get "/_cat/segments?v&format=json&h=index,shard,segment,size,size.memory" \
                                                                          "03-cat-segments.json" || \
  log "WARN" "cat/segments pode ser grande em clusters extensos; falha aqui nao e critica."
es_get "/_cat/count?v&format=json"                                       "03-cat-count-total.json"
es_get "/_alias"                                                          "03-alias-detail.json"

log "INFO" "03-indices-and-shards.sh concluido."
