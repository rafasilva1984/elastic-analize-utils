#!/usr/bin/env bash
# 06-snapshots.sh
# [GIT BASH - READ-ONLY]
# Coleta repositorios de snapshot e lista de snapshots por repositorio
# (paginado manualmente: repos primeiro, depois snapshots por repo).
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${DIR}/lib/common.sh"

check_dependencies || exit 1

es_get "/_snapshot"                                                         "06-snapshot-repositories.json"

REPOS_FILE="${EVIDENCE_DIR}/06-snapshot-repositories.json"
if [[ -f "${REPOS_FILE}" ]]; then
  repo_names="$(jq -r 'keys[]?' "${REPOS_FILE}" 2>/dev/null || true)"
  if [[ -n "${repo_names}" ]]; then
    while IFS= read -r repo; do
      safe_name="$(echo "${repo}" | tr -c 'A-Za-z0-9_.-' '_')"
      es_get "/_snapshot/${repo}/_all?verbose=false" "06-snapshots-${safe_name}.json" || \
        log "WARN" "Falha ao listar snapshots do repo '${repo}' (pode exigir permissao adicional)."
    done <<< "${repo_names}"
  else
    log "INFO" "Nenhum repositorio de snapshot encontrado ou resposta sem chaves."
  fi
else
  log "WARN" "Arquivo de repositorios nao encontrado; pulando listagem de snapshots por repo."
fi

log "INFO" "06-snapshots.sh concluido."
