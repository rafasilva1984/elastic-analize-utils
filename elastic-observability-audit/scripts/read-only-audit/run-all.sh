#!/usr/bin/env bash
# run-all.sh
# [GIT BASH - READ-ONLY]
# Orquestra a execucao sequencial de todos os scripts read-only da auditoria.
# Interrompe (mas nao "falha silenciosamente") se 00-check-env.sh falhar,
# pois isso indica variaveis de ambiente ou conectividade ausentes.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

scripts=(
  "00-check-env.sh"
  "01-cluster-overview.sh"
  "02-nodes.sh"
  "03-indices-and-shards.sh"
  "04-ilm.sh"
  "05-templates-and-pipelines.sh"
  "06-snapshots.sh"
  "07-ml-and-transforms.sh"
  "08-watcher.sh"
  "09-kibana-alerting.sh"
  "10-kibana-saved-objects.sh"
  "11-event-log-alert-history.sh"
)

for s in "${scripts[@]}"; do
  echo ""
  echo "==================== ${s} ===================="
  bash "${DIR}/${s}"
  rc=$?
  if [[ "${s}" == "00-check-env.sh" && "${rc}" -ne 0 ]]; then
    echo "Pre-requisitos ausentes (variaveis de ambiente/conectividade). Interrompendo run-all.sh."
    exit "${rc}"
  fi
done

echo ""
echo "Coleta read-only concluida. Evidencias em: $(cd "${DIR}/../../evidence" && pwd)"
echo "NENHUMA ALTERACAO FOI EXECUTADA NO CLUSTER."
