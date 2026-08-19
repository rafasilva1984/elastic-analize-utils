#!/usr/bin/env bash
# 00-check-env.sh
# [GIT BASH - READ-ONLY]
# Verifica dependencias, variaveis de ambiente e conectividade basica
# (GET / no Elasticsearch e GET /api/status no Kibana) sem alterar nada.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${DIR}/lib/common.sh"

echo "=== Verificando dependencias ==="
check_dependencies || { echo "Instale as dependencias faltantes e execute novamente."; exit 1; }

echo "=== Verificando variaveis de ambiente ==="
missing=0
for var in ELASTIC_URL KIBANA_URL; do
  if [[ -z "${!var:-}" ]]; then
    echo "  [FALTANDO] ${var}"
    missing=1
  else
    echo "  [OK] ${var} definido"
  fi
done

if [[ -n "${ELASTIC_API_KEY:-}" ]]; then
  echo "  [OK] ELASTIC_API_KEY definido (autenticacao por API Key sera usada)"
elif [[ -n "${ELASTIC_USERNAME:-}" && -n "${ELASTIC_PASSWORD:-}" ]]; then
  echo "  [OK] ELASTIC_USERNAME/ELASTIC_PASSWORD definidos (fallback basic auth)"
else
  echo "  [FALTANDO] Nenhuma credencial (ELASTIC_API_KEY ou ELASTIC_USERNAME+ELASTIC_PASSWORD)"
  missing=1
fi

if [[ "${missing}" -eq 1 ]]; then
  echo ""
  echo "Configure as variaveis ausentes antes de continuar. Exemplo (Git Bash, sem gravar valores em arquivos do repo):"
  echo '  read -s -p "ELASTIC_URL: " ELASTIC_URL; echo; export ELASTIC_URL'
  echo '  read -s -p "KIBANA_URL: " KIBANA_URL; echo; export KIBANA_URL'
  echo '  read -s -p "ELASTIC_API_KEY: " ELASTIC_API_KEY; echo; export ELASTIC_API_KEY'
  exit 2
fi

echo ""
echo "=== Testando conectividade (read-only) ==="
es_get "/" "00-es-root.json" && echo "  Elasticsearch: OK (ver evidence/00-es-root.json)"
kb_get "/api/status" "00-kibana-status.json" && echo "  Kibana: OK (ver evidence/00-kibana-status.json)"

echo ""
echo "Concluido. Nenhuma alteracao foi realizada no cluster."
