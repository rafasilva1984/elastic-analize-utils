#!/usr/bin/env bash
# common.sh - shared helpers for read-only audit scripts.
# [CLAUDE CODE / GIT BASH - READ-ONLY]
#
# ATENCAO: Este arquivo contem apenas funcoes auxiliares READ-ONLY.
# Nenhuma funcao aqui emite requisicoes de escrita (PUT/POST de criacao,
# DELETE, _update_by_query, _delete_by_query, reindex, etc).
#
# Uso: `source` este arquivo a partir de outro script da pasta read-only-audit.
#
# Variaveis de ambiente esperadas (ver README.md da auditoria):
#   ELASTIC_URL        - ex: https://es.exemplo.com:9200
#   KIBANA_URL          - ex: https://kibana.exemplo.com:5601
#   ELASTIC_API_KEY     - formato "id:api_key" (sera codificado em base64) OU ja base64
#   ELASTIC_USERNAME    - alternativa a API key
#   ELASTIC_PASSWORD    - alternativa a API key
#   ELASTIC_CLOUD_ID    - alternativa a ELASTIC_URL/KIBANA_URL (Elastic Cloud)
#   AUDIT_EVIDENCE_DIR  - opcional, default: ../../evidence relativo a este script
#   AUDIT_INSECURE      - opcional, "true" para pular verificacao TLS (NAO recomendado)

set -uo pipefail

SCRIPT_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUDIT_ROOT="$(cd "${SCRIPT_LIB_DIR}/../../.." && pwd)"
EVIDENCE_DIR="${AUDIT_EVIDENCE_DIR:-${AUDIT_ROOT}/evidence}"
LOG_DIR="${EVIDENCE_DIR}/_logs"

mkdir -p "${EVIDENCE_DIR}" "${LOG_DIR}"

TS="$(date -u +%Y%m%dT%H%M%SZ)"

log() {
  local level="$1"; shift
  local msg="$*"
  # Sanitizacao basica: nunca logar valores de variaveis de credencial.
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [${level}] ${msg}" | tee -a "${LOG_DIR}/audit-${TS%%T*}.log" >&2
}

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    log "ERROR" "Dependencia ausente: '${cmd}' nao encontrado no PATH. Instale-o (Git Bash: 'curl' e 'jq' costumam estar em https://curl.se e https://stedolan.github.io/jq/, ou via 'pacman'/'choco' conforme seu setup)."
    return 1
  fi
}

check_dependencies() {
  local ok=0
  require_cmd curl || ok=1
  require_cmd jq   || ok=1
  return "${ok}"
}

# Monta o header de autenticacao. Prioriza API Key.
build_auth_header() {
  if [[ -n "${ELASTIC_API_KEY:-}" ]]; then
    if [[ "${ELASTIC_API_KEY}" == *:* ]]; then
      # formato id:api_key -> precisa base64
      local encoded
      encoded="$(printf '%s' "${ELASTIC_API_KEY}" | base64 | tr -d '\n')"
      echo "Authorization: ApiKey ${encoded}"
    else
      echo "Authorization: ApiKey ${ELASTIC_API_KEY}"
    fi
  elif [[ -n "${ELASTIC_USERNAME:-}" && -n "${ELASTIC_PASSWORD:-}" ]]; then
    local encoded
    encoded="$(printf '%s:%s' "${ELASTIC_USERNAME}" "${ELASTIC_PASSWORD}" | base64 | tr -d '\n')"
    echo "Authorization: Basic ${encoded}"
  else
    return 1
  fi
}

resolve_es_url() {
  if [[ -n "${ELASTIC_URL:-}" ]]; then
    echo "${ELASTIC_URL%/}"
  elif [[ -n "${ELASTIC_CLOUD_ID:-}" ]]; then
    log "ERROR" "ELASTIC_CLOUD_ID definido mas resolucao automatica de URL a partir do Cloud ID nao esta implementada neste script (evita dependencia extra de base64/decode especifico). Defina ELASTIC_URL explicitamente (ex.: https://<deployment>.es.<region>.<provider>.cloud.es.io:9243)."
    return 1
  else
    log "ERROR" "ELASTIC_URL nao definido e ELASTIC_CLOUD_ID ausente."
    return 1
  fi
}

resolve_kibana_url() {
  if [[ -n "${KIBANA_URL:-}" ]]; then
    echo "${KIBANA_URL%/}"
  else
    log "ERROR" "KIBANA_URL nao definido."
    return 1
  fi
}

CURL_INSECURE_FLAG=()
if [[ "${AUDIT_INSECURE:-false}" == "true" ]]; then
  CURL_INSECURE_FLAG=(--insecure)
fi

# es_get <path> <output_file_basename>
# Executa GET read-only contra o Elasticsearch e salva em evidence/.
es_get() {
  local path="$1"
  local outfile="$2"
  local base auth
  base="$(resolve_es_url)" || return 1
  auth="$(build_auth_header)" || { log "ERROR" "Nenhuma credencial valida (ELASTIC_API_KEY ou ELASTIC_USERNAME/ELASTIC_PASSWORD)."; return 1; }

  local http_code
  http_code="$(curl -sS "${CURL_INSECURE_FLAG[@]}" -o "${EVIDENCE_DIR}/${outfile}" -w '%{http_code}' \
    -H "${auth}" -H 'Content-Type: application/json' \
    -X GET "${base}${path}")"

  if [[ "${http_code}" -ge 200 && "${http_code}" -lt 300 ]]; then
    log "INFO" "GET ${path} -> HTTP ${http_code} salvo em evidence/${outfile}"
  else
    log "ERROR" "GET ${path} -> HTTP ${http_code}. Ver evidence/${outfile} para detalhes do erro."
    return 1
  fi
}

# es_search_get <path incluindo _search> <query_json_file> <output_file_basename>
# Usado apenas para consultas _search (POST semanticamente read-only, ex: size:0 + aggs).
es_search_post() {
  local path="$1"
  local body_file="$2"
  local outfile="$3"
  local base auth
  base="$(resolve_es_url)" || return 1
  auth="$(build_auth_header)" || { log "ERROR" "Nenhuma credencial valida."; return 1; }

  local http_code
  http_code="$(curl -sS "${CURL_INSECURE_FLAG[@]}" -o "${EVIDENCE_DIR}/${outfile}" -w '%{http_code}' \
    -H "${auth}" -H 'Content-Type: application/json' \
    -X POST "${base}${path}" --data-binary "@${body_file}")"

  if [[ "${http_code}" -ge 200 && "${http_code}" -lt 300 ]]; then
    log "INFO" "POST(search) ${path} -> HTTP ${http_code} salvo em evidence/${outfile}"
  else
    log "ERROR" "POST(search) ${path} -> HTTP ${http_code}. Ver evidence/${outfile}."
    return 1
  fi
}

# kb_get <path> <output_file_basename>
kb_get() {
  local path="$1"
  local outfile="$2"
  local base auth
  base="$(resolve_kibana_url)" || return 1
  auth="$(build_auth_header)" || { log "ERROR" "Nenhuma credencial valida."; return 1; }

  local http_code
  http_code="$(curl -sS "${CURL_INSECURE_FLAG[@]}" -o "${EVIDENCE_DIR}/${outfile}" -w '%{http_code}' \
    -H "${auth}" -H 'kbn-xsrf: true' -H 'Content-Type: application/json' \
    -X GET "${base}${path}")"

  if [[ "${http_code}" -ge 200 && "${http_code}" -lt 300 ]]; then
    log "INFO" "GET(kibana) ${path} -> HTTP ${http_code} salvo em evidence/${outfile}"
  else
    log "ERROR" "GET(kibana) ${path} -> HTTP ${http_code}. Ver evidence/${outfile}."
    return 1
  fi
}

# kb_find_post <path> <query_json_file> <output_file_basename>
# Usado para endpoints Kibana de busca (ex: api/alerting/rules/_find, api/saved_objects/_find)
# que sao semanticamente read-only mesmo quando usam metodo GET com querystring longa
# ou POST de busca. Aqui tratamos apenas GET com querystring construida pelo chamador;
# mantido separado de kb_get por clareza de nomenclatura nos scripts que o chamam.
kb_find_get() {
  kb_get "$1" "$2"
}

sanitize_note() {
  cat <<'EOF'
NOTA DE SANITIZACAO: Os arquivos em evidence/ podem conter nomes de indices,
nomes de regras, nomes de conectores e metadados operacionais. Antes de
compartilhar qualquer evidencia fora do time responsavel, revise manualmente
por: tokens, API keys, senhas, cookies, PII ou URLs internas sensiveis.
Nenhum script desta pasta foi projetado para capturar corpos de documentos
de negocio (nao fazemos download indiscriminado de _source).
EOF
}
