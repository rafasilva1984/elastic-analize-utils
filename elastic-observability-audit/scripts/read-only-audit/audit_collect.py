#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit_collect.py
[WINDOWS PROMPT / GIT BASH / QUALQUER SHELL COM PYTHON 3.8+ - READ-ONLY]

Coleta READ-ONLY de evidencias de um cluster Elasticsearch/Kibana de
producao, equivalente aos scripts em scripts/read-only-audit/*.sh, porem
em Python puro (somente biblioteca padrao — nao requer "pip install").

ATENCAO: este script NUNCA executa PUT/POST de escrita, DELETE,
_update_by_query, _delete_by_query, _reindex, execucao de watch,
ativacao/desativacao de regra, teste de conector, ou qualquer chamada que
altere estado do cluster, do Kibana, de alertas, watches, ILM, templates,
pipelines, transforms, jobs de ML, snapshots, seguranca ou saved objects.
As unicas chamadas POST feitas aqui sao semanticamente read-only (busca:
_watcher/_query/watches, api/alerting/rules/_find, api/saved_objects/_find
via GET com querystring).

Uso (Windows - Prompt de Comando ou PowerShell ou Git Bash):

    set ELASTIC_URL=https://SEU-DEPLOYMENT.es.SUA-REGIAO.aws.found.io
    set KIBANA_URL=https://SEU-DEPLOYMENT.kb.SUA-REGIAO.aws.found.io
    python audit_collect.py

Se ELASTIC_API_KEY (ou ELASTIC_USERNAME/ELASTIC_PASSWORD) nao estiver
definida como variavel de ambiente, o script pergunta de forma segura no
proprio prompt (entrada mascarada via getpass, nunca aparece na tela nem
fica salva no historico do "set").

Nenhuma credencial e gravada em disco, logada ou impressa por este script.
As evidencias (JSON bruto retornado pelo cluster) sao salvas em
evidence/ (mesmo diretorio usado pelos scripts .sh), com os MESMOS nomes
de arquivo, para manter compatibilidade com current-state-inventory.md.
"""

import base64
import getpass
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuracao de caminhos
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent          # .../scripts/read-only-audit
AUDIT_ROOT = SCRIPT_DIR.parents[1]                      # .../elastic-observability-audit
EVIDENCE_DIR = Path(os.environ.get("AUDIT_EVIDENCE_DIR") or (AUDIT_ROOT / "evidence"))
LOG_DIR = EVIDENCE_DIR / "_logs"
TODAY = datetime.now(timezone.utc).strftime("%Y%m%d")
LOG_FILE = LOG_DIR / f"audit-{TODAY}.log"

DEFAULT_TIMEOUT = 30
LONG_TIMEOUT = 90  # para endpoints potencialmente mais pesados (_cluster/stats, _nodes/stats)
MAX_PAGES = 50


# ---------------------------------------------------------------------------
# Logging (nunca inclui credenciais)
# ---------------------------------------------------------------------------

def log(level, msg):
    line = "[{}] [{}] {}".format(
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), level, msg
    )
    print(line, file=sys.stderr)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Configuracao / credenciais
# ---------------------------------------------------------------------------

class Config:
    elastic_url = None
    kibana_url = None
    api_key = None          # ja pronto para o header "Authorization: ApiKey <valor>"
    username = None
    password = None


CONFIG = Config()


def _prompt_visible(label):
    try:
        return input("{}: ".format(label)).strip()
    except EOFError:
        return ""


def _prompt_secret(label):
    try:
        return getpass.getpass("{}: ".format(label)).strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def ensure_env():
    CONFIG.elastic_url = (os.environ.get("ELASTIC_URL") or "").strip()
    CONFIG.kibana_url = (os.environ.get("KIBANA_URL") or "").strip()
    raw_api_key = (os.environ.get("ELASTIC_API_KEY") or "").strip()
    CONFIG.username = (os.environ.get("ELASTIC_USERNAME") or "").strip()
    CONFIG.password = (os.environ.get("ELASTIC_PASSWORD") or "").strip()
    cloud_id = (os.environ.get("ELASTIC_CLOUD_ID") or "").strip()

    if not CONFIG.elastic_url:
        if cloud_id:
            log("ERROR",
                "ELASTIC_CLOUD_ID definido, mas a resolucao automatica de "
                "URL a partir do Cloud ID nao e feita por este script. "
                "Informe ELASTIC_URL explicitamente "
                "(ex.: https://<deployment>.es.<regiao>.<provedor>.cloud.es.io).")
        print("")
        CONFIG.elastic_url = _prompt_visible("ELASTIC_URL (ex.: https://SEU-DEPLOY.es.us-east-1.aws.found.io)")

    if not CONFIG.kibana_url:
        CONFIG.kibana_url = _prompt_visible("KIBANA_URL (ex.: https://SEU-DEPLOY.kb.us-east-1.aws.found.io)")

    if not raw_api_key and not (CONFIG.username and CONFIG.password):
        print("Nenhuma credencial encontrada nas variaveis de ambiente.")
        raw_api_key = _prompt_secret("ELASTIC_API_KEY (formato 'id:api_key' ou ja em base64; ENTER para pular)")
        if not raw_api_key:
            CONFIG.username = CONFIG.username or _prompt_visible("ELASTIC_USERNAME")
            CONFIG.password = CONFIG.password or _prompt_secret("ELASTIC_PASSWORD")

    CONFIG.elastic_url = CONFIG.elastic_url.rstrip("/")
    CONFIG.kibana_url = CONFIG.kibana_url.rstrip("/")

    if raw_api_key:
        if ":" in raw_api_key:
            encoded = base64.b64encode(raw_api_key.encode("utf-8")).decode("ascii")
            CONFIG.api_key = encoded
        else:
            CONFIG.api_key = raw_api_key

    if not CONFIG.elastic_url or not CONFIG.kibana_url:
        log("ERROR", "ELASTIC_URL e/ou KIBANA_URL ausentes. Abortando.")
        sys.exit(2)

    if not CONFIG.api_key and not (CONFIG.username and CONFIG.password):
        log("ERROR", "Nenhuma credencial valida (API Key ou usuario+senha). Abortando.")
        sys.exit(2)


def auth_header():
    if CONFIG.api_key:
        return {"Authorization": "ApiKey {}".format(CONFIG.api_key)}
    encoded = base64.b64encode(
        "{}:{}".format(CONFIG.username, CONFIG.password).encode("utf-8")
    ).decode("ascii")
    return {"Authorization": "Basic {}".format(encoded)}


# ---------------------------------------------------------------------------
# HTTP (somente stdlib) — nenhuma chamada de escrita
# ---------------------------------------------------------------------------

def _do_request(url, headers, method="GET", body=None, timeout=DEFAULT_TIMEOUT):
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        log("ERROR", "{} {} -> falha de conexao: {}".format(method, url, e.reason))
        return None, None
    except TimeoutError:
        log("ERROR", "{} {} -> timeout apos {}s".format(method, url, timeout))
        return None, None


def save_evidence(outfile, content_bytes):
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / outfile
    if content_bytes is None:
        content_bytes = b'{"error": "sem resposta (falha de conexao ou timeout)"}'
    path.write_bytes(content_bytes)
    return path


def _request_and_save(base_url, path, outfile, method="GET", body=None, timeout=DEFAULT_TIMEOUT, extra_headers=None):
    url = base_url + path
    headers = dict(auth_header())
    headers["Content-Type"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)

    code, data = _do_request(url, headers, method=method, body=body, timeout=timeout)
    save_evidence(outfile, data)

    if code is None:
        return None, None
    if 200 <= code < 300:
        log("INFO", "{} {} -> HTTP {} salvo em evidence/{}".format(method, path, code, outfile))
    else:
        log("ERROR", "{} {} -> HTTP {}. Ver evidence/{}".format(method, path, code, outfile))
    return code, data


def es_get(path, outfile, timeout=DEFAULT_TIMEOUT):
    return _request_and_save(CONFIG.elastic_url, path, outfile, method="GET", timeout=timeout)


def es_post(path, outfile, body_dict, timeout=DEFAULT_TIMEOUT):
    body = json.dumps(body_dict).encode("utf-8")
    return _request_and_save(CONFIG.elastic_url, path, outfile, method="POST", body=body, timeout=timeout)


def kb_get(path, outfile, timeout=DEFAULT_TIMEOUT):
    return _request_and_save(
        CONFIG.kibana_url, path, outfile, method="GET", timeout=timeout,
        extra_headers={"kbn-xsrf": "true"},
    )


def _safe_json(data):
    if not data:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# Conectividade inicial
# ---------------------------------------------------------------------------

def check_es_connectivity():
    code, _ = es_get("/", "00-es-root.json")
    return code is not None and 200 <= code < 300


def check_kibana_connectivity():
    code, _ = kb_get("/api/status", "00-kibana-status.json")
    return code is not None and 200 <= code < 300


# ---------------------------------------------------------------------------
# Fase 1 — Descoberta e inventario (equivalente aos scripts 01-10 .sh)
# ---------------------------------------------------------------------------

def cluster_overview():
    es_get("/_cluster/health?level=indices", "01-cluster-health.json")
    es_get("/_cluster/health?level=shards", "01-cluster-health-shards.json")
    es_get("/_license", "01-license.json")
    es_get("/_xpack", "01-xpack-features.json")
    es_get("/_cluster/settings?include_defaults=true&flat_settings=true", "01-cluster-settings.json")
    es_get("/_cluster/stats", "01-cluster-stats.json", timeout=LONG_TIMEOUT)
    es_get("/_cat/master?v&format=json", "01-cat-master.json")
    es_get("/_cat/health?v&format=json", "01-cat-health.json")
    es_get("/_cat/nodeattrs?v&format=json", "01-cat-nodeattrs.json")
    es_get("/_cat/plugins?v&format=json", "01-cat-plugins.json")


def nodes():
    es_get("/_nodes", "02-nodes-info.json", timeout=LONG_TIMEOUT)
    es_get("/_nodes/stats", "02-nodes-stats.json", timeout=LONG_TIMEOUT)
    es_get("/_nodes/stats/breaker", "02-nodes-breakers.json")
    es_get("/_nodes/stats/thread_pool", "02-nodes-threadpool.json")
    es_get("/_nodes/stats/indices/indexing_pressure", "02-nodes-indexing-pressure.json")
    es_get(
        "/_cat/nodes?v&format=json&h=name,node.role,heap.percent,heap.max,ram.percent,"
        "cpu,load_1m,load_5m,load_15m,disk.used_percent,disk.avail,master",
        "02-cat-nodes.json",
    )
    es_get(
        "/_cat/thread_pool?v&format=json&h=node_name,name,active,queue,rejected,size",
        "02-cat-threadpool.json",
    )
    es_get("/_cat/pending_tasks?v&format=json", "02-cat-pending-tasks.json")
    es_get("/_cat/recovery?v&format=json&active_only=true", "02-cat-recovery-active.json")
    es_get("/_tasks?detailed=false", "02-tasks-inflight.json")


def indices_and_shards():
    es_get(
        "/_cat/indices?v&format=json&h=index,health,status,pri,rep,docs.count,"
        "docs.deleted,store.size,pri.store.size,creation.date.string",
        "03-cat-indices.json",
        timeout=LONG_TIMEOUT,
    )
    es_get("/_cat/aliases?v&format=json", "03-cat-aliases.json")
    es_get(
        "/_cat/shards?v&format=json&h=index,shard,prirep,state,docs,store,node,unassigned.reason",
        "03-cat-shards.json",
        timeout=LONG_TIMEOUT,
    )
    es_get(
        "/_cat/shards?v&format=json&h=index,shard,prirep,state,unassigned.reason&s=state",
        "03-cat-shards-by-state.json",
        timeout=LONG_TIMEOUT,
    )
    es_get("/_data_stream", "03-data-streams.json")
    es_get(
        "/_cat/segments?v&format=json&h=index,shard,segment,size,size.memory",
        "03-cat-segments.json",
        timeout=LONG_TIMEOUT,
    )
    es_get("/_cat/count?v&format=json", "03-cat-count-total.json")
    es_get("/_alias", "03-alias-detail.json")


def ilm():
    es_get("/_ilm/policy", "04-ilm-policies.json")
    es_get("/_ilm/status", "04-ilm-status.json")
    es_get("/_all/_ilm/explain?only_errors=false&only_managed=true", "04-ilm-explain-managed.json", timeout=LONG_TIMEOUT)
    es_get("/_all/_ilm/explain?only_errors=true", "04-ilm-explain-errors.json")
    es_get("/_slm/policy", "04-slm-policies.json")
    es_get("/_slm/stats", "04-slm-stats.json")


def templates_and_pipelines():
    es_get("/_index_template", "05-index-templates.json")
    es_get("/_component_template", "05-component-templates.json")
    es_get("/_template", "05-legacy-templates.json")
    es_get("/_ingest/pipeline", "05-ingest-pipelines.json")
    es_get("/_ingest/geoip/stats", "05-geoip-stats.json")


def snapshots():
    code, data = es_get("/_snapshot", "06-snapshot-repositories.json")
    if code is None or not (200 <= code < 300):
        return
    parsed = _safe_json(data)
    if not isinstance(parsed, dict) or not parsed:
        log("INFO", "Nenhum repositorio de snapshot encontrado ou resposta sem chaves.")
        return
    for repo in parsed.keys():
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", repo)
        encoded_repo = urllib.parse.quote(repo, safe="")
        code2, _ = es_get(
            "/_snapshot/{}/_all?verbose=false".format(encoded_repo),
            "06-snapshots-{}.json".format(safe_name),
        )
        if code2 is None or not (200 <= code2 < 300):
            log("WARN", "Falha ao listar snapshots do repo '{}' (pode exigir permissao adicional).".format(repo))


def ml_and_transforms():
    es_get("/_ml/anomaly_detectors", "07-ml-jobs.json")
    es_get("/_ml/anomaly_detectors/_stats", "07-ml-jobs-stats.json")
    es_get("/_ml/datafeeds", "07-ml-datafeeds.json")
    es_get("/_ml/datafeeds/_stats", "07-ml-datafeeds-stats.json")
    es_get("/_ml/trained_models", "07-ml-trained-models.json")
    es_get("/_transform", "07-transforms.json")
    es_get("/_transform/_stats", "07-transforms-stats.json")


def watcher():
    es_get("/_watcher/stats?metric=all", "08-watcher-stats.json")
    body = {"size": 1000}
    code, _ = es_post("/_watcher/_query/watches", "08-watches-query.json", body)
    if code is None or not (200 <= code < 300):
        log("WARN", "POST /_watcher/_query/watches falhou (endpoint pode nao existir nesta "
                     "versao, ou Watcher pode estar desabilitado).")


def event_log_alert_history():
    body = {
        "size": 0,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"event.provider": "alerting"}},
                    {"range": {"@timestamp": {"gte": "now-90d"}}},
                ]
            }
        },
        "aggs": {
            "by_rule": {
                "terms": {"field": "rule.id", "size": 2000},
                "aggs": {
                    "by_action": {"terms": {"field": "event.action", "size": 20}},
                    "by_outcome": {"terms": {"field": "event.outcome", "size": 5}},
                    "avg_duration_ns": {"avg": {"field": "event.duration"}},
                    "first_event": {"min": {"field": "@timestamp"}},
                    "last_event": {"max": {"field": "@timestamp"}},
                },
            },
            "total_events_by_action": {"terms": {"field": "event.action", "size": 20}},
        },
    }
    code, _ = es_post("/.kibana-event-log-*/_search", "11-event-log-alert-history-90d.json", body, timeout=LONG_TIMEOUT)
    if code is None or not (200 <= code < 300):
        log("WARN", "Busca em .kibana-event-log-* falhou (indice pode nao existir ainda, ou "
                     "nao ha eventos no periodo). Isso fecharia a lacuna de historico de 90 dias "
                     "de alert-fatigue-analysis.md/blind-spots.md.")


def _paginate_kibana(make_path, outfile_template, label, data_key, total_key, per_page=100):
    page = 1
    while page <= MAX_PAGES:
        path = make_path(page, per_page)
        outfile = outfile_template.format(page=page)
        code, data = kb_get(path, outfile)
        if code is None or not (200 <= code < 300):
            break
        parsed = _safe_json(data)
        if not isinstance(parsed, dict):
            log("WARN", "{} pagina {}: resposta nao e um objeto JSON valido.".format(label, page))
            break
        items = parsed.get(data_key, [])
        got = len(items) if isinstance(items, list) else 0
        total = parsed.get(total_key, "?")
        log("INFO", "{} pagina {}: {} itens (total reportado: {})".format(label, page, got, total))
        if got < per_page or got == 0:
            break
        page += 1
    if page > MAX_PAGES:
        log("WARN", "Interrompendo paginacao de '{}' apos {} paginas por seguranca.".format(label, MAX_PAGES))


def kibana_alerting():
    _paginate_kibana(
        lambda page, per_page: "/api/alerting/rules/_find?per_page={}&page={}".format(per_page, page),
        "09-kibana-alerting-rules-page{page}.json",
        "Regras de alerting",
        data_key="data",
        total_key="total",
    )
    kb_get("/api/actions/connectors", "09-kibana-connectors.json")
    kb_get("/api/alerting/rule_types", "09-kibana-rule-types.json")


def kibana_saved_objects():
    types = [
        ("index-pattern", "dataviews"),
        ("dashboard", "dashboards"),
        ("visualization", "visualizations"),
        ("lens", "lens"),
        ("search", "saved-searches"),
        ("map", "maps"),
    ]
    for so_type, label in types:
        def make_path(page, per_page, so_type=so_type):
            return "/api/saved_objects/_find?type={}&per_page={}&page={}".format(so_type, per_page, page)

        _paginate_kibana(
            make_path,
            "10-so-{}-page{{page}}.json".format(label),
            label,
            data_key="saved_objects",
            total_key="total",
        )


# ---------------------------------------------------------------------------
# Orquestracao
# ---------------------------------------------------------------------------

def main():
    print("=== audit_collect.py — coleta READ-ONLY Elasticsearch/Kibana ===")
    print("Nenhuma alteracao sera executada no cluster.\n")

    ensure_env()

    log("INFO", "Testando conectividade (read-only)...")
    es_ok = check_es_connectivity()
    kb_ok = check_kibana_connectivity()

    if not es_ok:
        log("ERROR",
            "Elasticsearch inacessivel em '{}'. Verifique ELASTIC_URL, credenciais e rede. "
            "Abortando coleta.".format(CONFIG.elastic_url))
        sys.exit(2)
    log("INFO", "Elasticsearch OK.")

    if not kb_ok:
        log("WARN",
            "Kibana inacessivel em '{}'. Prosseguindo apenas com a coleta do Elasticsearch "
            "(regras de alerting, conectores e saved objects nao serao coletados).".format(CONFIG.kibana_url))
    else:
        log("INFO", "Kibana OK.")

    steps = [
        ("Visao geral do cluster", cluster_overview),
        ("Nos", nodes),
        ("Indices e shards", indices_and_shards),
        ("ILM/SLM", ilm),
        ("Templates e ingest pipelines", templates_and_pipelines),
        ("Snapshots", snapshots),
        ("Machine Learning e Transforms", ml_and_transforms),
        ("Watcher", watcher),
        ("Historico de alertas (90 dias, .kibana-event-log-*)", event_log_alert_history),
    ]
    if kb_ok:
        steps.append(("Kibana Alerting e Conectores", kibana_alerting))
        steps.append(("Kibana Saved Objects", kibana_saved_objects))

    for label, fn in steps:
        log("INFO", "--- {} ---".format(label))
        try:
            fn()
        except Exception as exc:  # nao aborta a coleta inteira por falha isolada
            log("ERROR", "Falha inesperada em '{}': {}".format(label, exc))

    print("")
    print("Coleta concluida. Evidencias em: {}".format(EVIDENCE_DIR))
    print("Revise evidence/ manualmente antes de qualquer commit ou compartilhamento "
          "(ver evidence/README.md).")
    print("NENHUMA ALTERACAO FOI EXECUTADA NO CLUSTER.")


if __name__ == "__main__":
    main()
