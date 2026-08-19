#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit_analyze.py
[LOCAL - 100% OFFLINE, SEM REDE, SEM GIT]

Analisa localmente as evidencias ja coletadas por audit_collect.py (pasta
evidence/) e gera automaticamente:

  report/alert-inventory.csv          (1 linha por regra de alerta)
  report/alert-recommendations.csv    (subconjunto acionavel, com evidencia)
  report/prioritized-backlog.csv      (achados P0-P3 de todas as categorias)
  report/findings-summary.md          (resumo legivel dos achados)

Nao acessa a rede, nao acessa o Git, nao depende de nenhuma ferramenta
externa alem da biblioteca padrao do Python 3.8+. Pode ser executado
quantas vezes forem necessarias, em qualquer maquina que tenha uma copia
da pasta evidence/ (nao precisa nem ser um clone do repositorio Git).

Uso (Windows, apos rodar audit_collect.py):

    python audit_analyze.py

As saidas vao para elastic-observability-audit/report/ por padrao. Para
manter relatorios separados de ambientes/clusters diferentes, aponte
AUDIT_REPORT_DIR para uma pasta distinta a cada execucao:

    set AUDIT_REPORT_DIR=C:\relatorios\cluster-producao-br
    python audit_analyze.py

A logica de analise e generica (thresholds parametrizados via variaveis
de ambiente, ver secao CONFIG abaixo) - nao ha nenhum nome de cluster,
regra ou indice fixado no codigo. O mesmo script funciona para qualquer
evidence/ gerado por audit_collect.py, de qualquer cluster.

Limitacoes conhecidas (a ferramenta e honesta sobre o que nao sabe):
  - Classificacoes de "duplicata"/"sem uso" aqui sao heuristicas
    automaticas baseadas em assinatura de conteudo; nao substituem
    verificacao humana antes de qualquer exclusao.
  - Historico de 90 dias de alertas so e preenchido se
    evidence/11-event-log-alert-history-90d.json existir (gerado por
    audit_collect.py::event_log_alert_history()); caso contrario, os
    campos ficam "ND".
"""

import csv
import glob
import json
import os
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIG (parametrizavel via variaveis de ambiente, com defaults sensatos)
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
AUDIT_ROOT = SCRIPT_DIR.parents[1]
EVIDENCE_DIR = Path(os.environ.get("AUDIT_EVIDENCE_DIR") or (AUDIT_ROOT / "evidence"))
REPORT_DIR = Path(os.environ.get("AUDIT_REPORT_DIR") or (AUDIT_ROOT / "report"))

CFG = {
    # circuit breaker "tripped" acumulado acima disso => achado P0
    "BREAKER_TRIPPED_P0": int(os.environ.get("AUDIT_THRESHOLD_BREAKER_P0", "1000")),
    "BREAKER_TRIPPED_P1": int(os.environ.get("AUDIT_THRESHOLD_BREAKER_P1", "1")),
    # old-gen GC count acumulado acima disso => achado (sinal de pressao de heap)
    "OLD_GC_COUNT_WARN": int(os.environ.get("AUDIT_THRESHOLD_OLD_GC", "50")),
    # CPU/RAM percentuais para considerar saturado
    "CPU_SATURATED_PCT": int(os.environ.get("AUDIT_THRESHOLD_CPU", "90")),
    "RAM_SATURATED_PCT": int(os.environ.get("AUDIT_THRESHOLD_RAM", "95")),
    # razao (tamanho real do shard / limite configurado no ILM) acima disso => achado
    "SHARD_OVERSIZE_RATIO": float(os.environ.get("AUDIT_THRESHOLD_SHARD_RATIO", "1.5")),
    # dias sem dado novo em job de ML aberto para considerar "provavelmente sem uso"
    "ML_STALE_DAYS": int(os.environ.get("AUDIT_THRESHOLD_ML_STALE_DAYS", "90")),
    # dashboards nao editados ha mais de N dias
    "DASHBOARD_STALE_DAYS": int(os.environ.get("AUDIT_THRESHOLD_DASHBOARD_STALE_DAYS", "365")),
    # taxa de falha de snapshot (%) acima disso => achado
    "SLM_FAILURE_RATE_PCT": float(os.environ.get("AUDIT_THRESHOLD_SLM_FAILURE_PCT", "5.0")),
    # tipos de regra em que "sem acoes" e um padrao aceitavel da propria feature
    "NO_ACTION_EXEMPT_RULE_TYPES": set(
        (os.environ.get("AUDIT_NO_ACTION_EXEMPT_TYPES") or "slo.rules.burnRate").split(",")
    ),
}

NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Helpers genericos
# ---------------------------------------------------------------------------

def log(msg):
    print("[audit_analyze] {}".format(msg), file=sys.stderr)


def load_json(filename, default=None):
    path = EVIDENCE_DIR / filename
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log("AVISO: falha ao ler {} ({}), ignorando.".format(filename, exc))
        return default


def load_json_glob(pattern):
    """Carrega e concatena varias paginas (ex.: 09-kibana-alerting-rules-page*.json)."""
    out = []
    for f in sorted(glob.glob(str(EVIDENCE_DIR / pattern))):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                out.append((os.path.basename(f), json.load(fh)))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            log("AVISO: falha ao ler {} ({}), ignorando.".format(f, exc))
    return out


def human_size_to_bytes(size_str):
    """Converte '119.4gb', '792mb', '82kb' etc. (formato _cat/*) para bytes."""
    if not size_str:
        return None
    m = re.match(r"^([0-9.]+)\s*([a-zA-Z]*)$", str(size_str).strip())
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2).lower()
    mult = {
        "": 1, "b": 1,
        "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4, "pb": 1000**5,
    }.get(unit, 1)
    return num * mult


def parse_es_timestamp_ms(ms):
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def days_since(dt):
    if dt is None:
        return None
    return (NOW - dt).days


def ensure_report_dir():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Coletor de achados (lista unica, priorizada no final)
# ---------------------------------------------------------------------------

class Finding:
    __slots__ = ("category", "priority", "title", "evidence", "detail")

    def __init__(self, category, priority, title, evidence, detail=""):
        self.category = category
        self.priority = priority
        self.title = title
        self.evidence = evidence
        self.detail = detail


FINDINGS = []


def add_finding(category, priority, title, evidence, detail=""):
    FINDINGS.append(Finding(category, priority, title, evidence, detail))


# ---------------------------------------------------------------------------
# Analise: cluster / nos / breakers / GC
# ---------------------------------------------------------------------------

def analyze_nodes():
    nodes_stats = load_json("02-nodes-stats.json")
    cat_nodes = load_json("02-cat-nodes.json") or []
    nodeattrs = load_json("01-cat-nodeattrs.json") or []

    if not nodes_stats:
        log("02-nodes-stats.json ausente - pulando analise de nos.")
        return

    tier_by_node = {a["node"]: a["value"] for a in nodeattrs if a.get("attr") == "data"}
    cat_by_name = {n["name"]: n for n in cat_nodes}

    for node_id, n in (nodes_stats.get("nodes") or {}).items():
        name = n.get("name", node_id)
        tier = tier_by_node.get(name, "n/a")
        breakers = n.get("breakers") or {}
        parent = breakers.get("parent", {})
        tripped = parent.get("tripped", 0)
        gc = ((n.get("jvm") or {}).get("gc") or {}).get("collectors") or {}
        old_gc_count = ((gc.get("old") or {}).get("collection_count")) or 0
        old_gc_time = ((gc.get("old") or {}).get("collection_time_in_millis")) or 0
        heap_max = ((n.get("jvm") or {}).get("mem") or {}).get("heap_max_in_bytes")
        cn = cat_by_name.get(name, {})
        cpu = cn.get("cpu")
        ram_pct = cn.get("ram.percent")

        if tripped >= CFG["BREAKER_TRIPPED_P0"]:
            add_finding(
                "Performance", "P0",
                "Circuit breaker 'parent' disparando com alta frequencia no no {} (tier {})".format(name, tier),
                "evidence/02-nodes-breakers.json ou 02-nodes-stats.json: node={} tripped={} heap_max_bytes={}".format(name, tripped, heap_max),
                "Indica rejeicao de requisicoes por falta de memoria. Investigar dimensionamento de heap do tier '{}' e padrao de consultas.".format(tier),
            )
        elif tripped >= CFG["BREAKER_TRIPPED_P1"]:
            add_finding(
                "Performance", "P2",
                "Circuit breaker 'parent' disparou pelo menos 1 vez no no {} (tier {})".format(name, tier),
                "evidence/02-nodes-stats.json: node={} tripped={}".format(name, tripped),
                "Volume baixo de disparos; monitorar tendencia.",
            )

        if old_gc_count >= CFG["OLD_GC_COUNT_WARN"]:
            add_finding(
                "Performance", "P1",
                "GC de old-gen elevado no no {} (tier {}) - {} coletas, {} ms cumulativos".format(name, tier, old_gc_count, old_gc_time),
                "evidence/02-nodes-stats.json: node={} jvm.gc.collectors.old".format(name),
                "Sinal de pressao de memoria sustentada; correlacionar com breaker trips e heap_max do no.",
            )

        try:
            if cpu is not None and float(cpu) >= CFG["CPU_SATURATED_PCT"]:
                add_finding(
                    "Performance", "P1",
                    "CPU saturada no no {} (tier {}): {}%".format(name, tier, cpu),
                    "evidence/02-cat-nodes.json: name={} cpu={}".format(name, cpu),
                )
        except (TypeError, ValueError):
            pass
        try:
            if ram_pct is not None and float(ram_pct) >= CFG["RAM_SATURATED_PCT"]:
                add_finding(
                    "Performance", "P1",
                    "RAM saturada no no {} (tier {}): {}%".format(name, tier, ram_pct),
                    "evidence/02-cat-nodes.json: name={} ram.percent={}".format(name, ram_pct),
                )
        except (TypeError, ValueError):
            pass


# ---------------------------------------------------------------------------
# Analise: shards vs limiar de rollover ILM
# ---------------------------------------------------------------------------

def analyze_shard_sizes():
    indices = load_json("03-cat-indices.json")
    ilm_policies = load_json("04-ilm-policies.json")
    ilm_explain = load_json("04-ilm-explain-managed.json")

    if not indices or not ilm_policies or not ilm_explain:
        log("Dados insuficientes (indices/ilm-policies/ilm-explain) - pulando analise de shard size.")
        return

    policy_limits = {}
    for name, p in ilm_policies.items():
        try:
            rollover = p["policy"]["phases"]["hot"]["actions"].get("rollover", {})
            size_limit = rollover.get("max_primary_shard_size")
            if size_limit:
                policy_limits[name] = human_size_to_bytes(size_limit)
        except (KeyError, TypeError):
            continue

    policy_by_index = {}
    for idx, info in (ilm_explain.get("indices") or {}).items():
        pol = info.get("policy")
        if pol:
            policy_by_index[idx.lstrip(".")] = pol
            policy_by_index[idx] = pol

    for row in indices:
        idx_name = row.get("index")
        pol = policy_by_index.get(idx_name)
        if not pol or pol not in policy_limits:
            continue
        limit = policy_limits[pol]
        actual = human_size_to_bytes(row.get("store.size"))
        if limit and actual and actual / limit >= CFG["SHARD_OVERSIZE_RATIO"]:
            add_finding(
                "Performance", "P1",
                "Indice '{}' com {:.1f}x o limite de rollover configurado na politica ILM '{}'".format(
                    idx_name, actual / limit, pol),
                "evidence/03-cat-indices.json + evidence/04-ilm-policies.json: store.size={}, max_primary_shard_size configurado={}".format(
                    row.get("store.size"), ilm_policies[pol]["policy"]["phases"]["hot"]["actions"]["rollover"].get("max_primary_shard_size")),
                "Shards muito maiores que o limite configurado aumentam tempo de recuperacao e reduzem paralelismo de busca.",
            )


# ---------------------------------------------------------------------------
# Analise: Transforms
# ---------------------------------------------------------------------------

def analyze_transforms():
    stats = load_json("07-transforms-stats.json")
    if not stats:
        log("07-transforms-stats.json ausente - pulando analise de transforms.")
        return
    for t in stats.get("transforms", []):
        health = (t.get("health") or {}).get("status")
        if health == "red":
            issues = (t.get("health") or {}).get("issues") or []
            detail = "; ".join(i.get("details", i.get("issue", "")) for i in issues)[:400]
            add_finding(
                "Resiliencia", "P1",
                "Transform '{}' em falha ativa (health=red)".format(t.get("id")),
                "evidence/07-transforms-stats.json: id={}".format(t.get("id")),
                detail,
            )


# ---------------------------------------------------------------------------
# Analise: Jobs de ML obsoletos/abertos sem dado novo
# ---------------------------------------------------------------------------

def analyze_ml_jobs():
    stats = load_json("07-ml-jobs-stats.json")
    if not stats:
        log("07-ml-jobs-stats.json ausente - pulando analise de ML.")
        return
    for j in stats.get("jobs", []):
        ts_ms = (j.get("data_counts") or {}).get("latest_record_timestamp")
        dt = parse_es_timestamp_ms(ts_ms)
        d = days_since(dt)
        if d is not None and d > CFG["ML_STALE_DAYS"]:
            state = j.get("state")
            prio = "P2" if state == "opened" else "P3"
            add_finding(
                "FinOps/Limpeza", prio,
                "Job de ML '{}' ({}) sem dado novo ha {} dias".format(j.get("job_id"), state, d),
                "evidence/07-ml-jobs-stats.json: job_id={} latest_record_timestamp={}".format(j.get("job_id"), ts_ms),
                "Job 'opened' consome memoria/CPU no no de ML mesmo sem processar dados novos." if state == "opened" else "",
            )


# ---------------------------------------------------------------------------
# Analise: SLM (falha de snapshot)
# ---------------------------------------------------------------------------

def analyze_slm():
    stats = load_json("04-slm-stats.json")
    if not stats:
        return
    taken = stats.get("total_snapshots_taken", 0)
    failed = stats.get("total_snapshots_failed", 0)
    if taken:
        rate = 100.0 * failed / taken
        if rate >= CFG["SLM_FAILURE_RATE_PCT"]:
            add_finding(
                "Resiliencia", "P1",
                "Taxa de falha de snapshot elevada: {:.2f}% ({} de {})".format(rate, failed, taken),
                "evidence/04-slm-stats.json",
            )


# ---------------------------------------------------------------------------
# Analise: Regras de Alerting (o nucleo do alert-fatigue)
# ---------------------------------------------------------------------------

OBJECTIVE_BY_TYPE = {
    ".es-query": "Consulta ES/ESQL customizada definida pelo time",
    "xpack.uptime.alerts.monitorStatus": "Monitorar status de disponibilidade de um monitor Uptime",
    "siem.queryRule": "Deteccao de seguranca (regra SIEM baseada em consulta)",
    "siem.mlRule": "Deteccao de seguranca baseada em anomalia de ML",
    "siem.thresholdRule": "Deteccao de seguranca baseada em limiar de eventos",
    ".index-threshold": "Alerta de limiar sobre agregacao de indice",
    "slo.rules.burnRate": "Alerta de burn-rate de um SLO",
    "xpack.ml.anomaly_detection_alert": "Alerta de anomalia de um job de Machine Learning",
    "observability.rules.custom_threshold": "Alerta de limiar customizado (Observability)",
    "xpack.synthetics.alerts.monitorStatus": "Monitorar status de um monitor Synthetics",
    "xpack.synthetics.alerts.tls": "Alerta de expiracao/validade de certificado TLS",
    "transform_health": "Monitorar saude/execucao de um Transform",
    "apm.anomaly": "Alerta de anomalia APM",
    "apm.transaction_error_rate": "Alerta de taxa de erro de transacao APM",
}


def norm_query(r):
    p = r.get("params", {}) or {}
    rtype = r.get("rule_type_id")
    if rtype == ".es-query":
        q = p.get("esQuery")
        if not q and isinstance(p.get("searchConfiguration"), dict):
            q = ((p["searchConfiguration"].get("query") or {}).get("query"))
        if not q and isinstance(p.get("esqlQuery"), dict):
            q = p["esqlQuery"].get("esql")
        return re.sub(r"\s+", " ", str(q or "")).strip()
    if rtype == "siem.queryRule":
        return re.sub(r"\s+", " ", str(p.get("query", ""))).strip()
    if rtype in (".index-threshold", "observability.rules.custom_threshold"):
        return json.dumps(p.get("criteria", []), sort_keys=True)
    return ""


def index_or_dv(r):
    p = r.get("params", {}) or {}
    if p.get("index"):
        idx = p["index"]
        return ",".join(idx) if isinstance(idx, list) else str(idx)
    sc = p.get("searchConfiguration", {})
    if isinstance(sc, dict) and sc.get("index"):
        return str(sc["index"])
    return "-"


def load_all_rules():
    rules = []
    for fname, d in load_json_glob("09-kibana-alerting-rules-page*.json"):
        for r in d.get("data", []):
            r["_evidence_file"] = fname
            rules.append(r)
    return rules


def load_event_log_history():
    """Retorna dict rule_id -> stats de 90 dias, se o arquivo existir."""
    d = load_json("11-event-log-alert-history-90d.json")
    if not d:
        return {}
    out = {}
    for bucket in (((d.get("aggregations") or {}).get("by_rule") or {}).get("buckets") or []):
        rule_id = bucket.get("key")
        by_action = {b["key"]: b["doc_count"] for b in (bucket.get("by_action", {}).get("buckets") or [])}
        by_outcome = {b["key"]: b["doc_count"] for b in (bucket.get("by_outcome", {}).get("buckets") or [])}
        avg_ns = (bucket.get("avg_duration_ns") or {}).get("value")
        out[rule_id] = {
            "fires_90d": by_action.get("new-instance", 0),
            "recoveries_90d": by_action.get("recovered-instance", 0),
            "executions_90d": by_action.get("execute", 0),
            "execution_failures_90d": by_outcome.get("failure", 0),
            "avg_duration_ms": round(avg_ns / 1e6, 1) if avg_ns else None,
            "first_event": bucket.get("first_event", {}).get("value_as_string"),
            "last_event": bucket.get("last_event", {}).get("value_as_string"),
        }
    return out


def analyze_alert_rules():
    rules = load_all_rules()
    if not rules:
        log("Nenhuma regra de alerting encontrada em evidence/09-kibana-alerting-rules-page*.json - pulando.")
        return [], {}

    event_log = load_event_log_history()
    if event_log:
        log("Historico de 90 dias encontrado para {} regras (evidence/11-event-log-alert-history-90d.json).".format(len(event_log)))
    else:
        log("Sem historico de 90 dias (evidence/11-event-log-alert-history-90d.json ausente) - "
            "campos fires_90d/recoveries_90d ficarao 'ND'. Rode audit_collect.py atualizado para gerar este arquivo.")

    # --- deteccao de duplicatas por assinatura de conteudo (generico) ---
    sig_groups = {}
    for r in rules:
        sig = (r.get("rule_type_id"), index_or_dv(r), norm_query(r))
        if sig[2]:  # so agrupa quando ha consulta/condicao extraivel
            sig_groups.setdefault(sig, []).append(r)

    for sig, group in sig_groups.items():
        if len(group) > 1:
            names = ", ".join(g["name"] for g in group[:5])
            add_finding(
                "Duplicidade", "P2",
                "{} regras com mesma assinatura (tipo+indice+consulta normalizada): {}".format(len(group), names),
                "evidence/09-kibana-alerting-rules-page*.json: ids={}".format(",".join(g["id"][:8] for g in group)),
                "Assinatura identica sugere duplicata exata ou copia nao intencional - verificar manualmente antes de consolidar.",
            )

    # --- regras ativas com erro ---
    for r in rules:
        st = r.get("execution_status") or {}
        if r.get("enabled") and st.get("status") == "error":
            err = st.get("error") or {}
            add_finding(
                "Alerting", "P1",
                "Regra ativa em erro: '{}' ({})".format(r.get("name"), r.get("rule_type_id")),
                "evidence/{}: id={} error={}".format(r["_evidence_file"], r["id"], str(err.get("message", ""))[:200]),
            )
        elif not r.get("enabled") and st.get("status") == "error":
            err = st.get("error") or {}
            add_finding(
                "Governanca", "P3",
                "Regra desabilitada com ultima execucao em erro (candidata a exclusao apos observacao): '{}'".format(r.get("name")),
                "evidence/{}: id={} error={}".format(r["_evidence_file"], r["id"], str(err.get("message", ""))[:200]),
            )

    # --- ativas sem acoes (exceto tipos isentos) ---
    for r in rules:
        if r.get("enabled") and not r.get("actions") and r.get("rule_type_id") not in CFG["NO_ACTION_EXEMPT_RULE_TYPES"]:
            add_finding(
                "Alerting", "P2",
                "Regra ativa sem nenhuma acao/notificacao configurada: '{}'".format(r.get("name")),
                "evidence/{}: id={} actions=[]".format(r["_evidence_file"], r["id"]),
            )

    return rules, event_log


# ---------------------------------------------------------------------------
# Analise: Saved objects orfaos
# ---------------------------------------------------------------------------

def analyze_saved_objects():
    def load_all(pattern):
        objs = []
        for fname, d in load_json_glob(pattern):
            objs.extend(d.get("saved_objects", []))
        return objs

    dataviews = load_all("10-so-dataviews-page*.json")
    dashboards = load_all("10-so-dashboards-page*.json")
    viz = load_all("10-so-visualizations-page*.json")
    lens = load_all("10-so-lens-page*.json")
    searches = load_all("10-so-saved-searches-page*.json")
    maps = load_all("10-so-maps-page*.json")

    if not dataviews:
        log("Sem data views em evidence/ - pulando analise de saved objects orfaos.")
        return

    referenced = set()
    for lst in (dashboards, viz, lens, searches, maps):
        for o in lst:
            for ref in o.get("references", []):
                if ref.get("type") == "index-pattern":
                    referenced.add(ref.get("id"))

    rules_blob = ""
    rules = load_all_rules()
    if rules:
        rules_blob = json.dumps(rules)

    dv_ids = {d["id"] for d in dataviews}
    referenced_by_rules = {i for i in dv_ids if rules_blob and i in rules_blob}
    unused = dv_ids - referenced - referenced_by_rules

    if unused:
        add_finding(
            "Governanca", "P3",
            "{} de {} data views ({:.0f}%) sem referencia detectavel em dashboards/viz/lens/searches/maps/alertas".format(
                len(unused), len(dv_ids), 100.0 * len(unused) / len(dv_ids)),
            "evidence/10-so-dataviews-page*.json cruzado com references[] de outros saved objects e params de regras",
            "Nao prova ausencia de uso (Discover ad-hoc nao deixa rastro) - classificar como 'provavelmente sem uso', nao excluir sem confirmacao.",
        )

    stale_dash = []
    for d in dashboards:
        dt = parse_iso(d.get("updated_at") or d.get("created_at"))
        ds = days_since(dt)
        if ds is not None and ds > CFG["DASHBOARD_STALE_DAYS"]:
            stale_dash.append(d)
    if stale_dash:
        add_finding(
            "Governanca", "P3",
            "{} de {} dashboards nao editados ha mais de {} dias".format(len(stale_dash), len(dashboards), CFG["DASHBOARD_STALE_DAYS"]),
            "evidence/10-so-dashboards-page*.json: campo updated_at",
        )

    empty_dash = [d for d in dashboards if d.get("attributes", {}).get("panelsJSON") in ("[]", None, "")]
    if empty_dash:
        add_finding(
            "Governanca", "P3",
            "{} dashboards com panelsJSON vazio (estruturalmente sem paineis)".format(len(empty_dash)),
            "evidence/10-so-dashboards-page*.json",
        )


# ---------------------------------------------------------------------------
# Saida: CSVs
# ---------------------------------------------------------------------------

ALERT_INVENTORY_COLS = [
    "id", "name", "type", "source", "objective", "index_or_dataview",
    "query_or_condition_normalized", "schedule_interval", "lookback", "threshold",
    "recovery_condition", "fires_90d", "recoveries_90d", "avg_time_to_recover",
    "total_time_active", "flapping_detected", "repeated_same_event",
    "peak_recurrence_hours", "execution_failures_90d", "connector_failures_90d",
    "severity", "grouping", "throttle", "notification_channel", "owner",
    "runbook_url", "operator_context_present", "expected_action",
    "technical_impact", "business_impact", "evidence_file",
]


def write_alert_inventory(rules, event_log):
    if not rules:
        return
    path = REPORT_DIR / "alert-inventory.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ALERT_INVENTORY_COLS, extrasaction="ignore")
        w.writeheader()
        for r in rules:
            actions = r.get("actions") or []
            action_types = ",".join(sorted(set(
                a.get("action_type_id") or a.get("actionTypeId") or "?" for a in actions
            ))) if actions else "SEM_ACOES"
            hist = event_log.get(r["id"], {})
            p = r.get("params") or {}
            w.writerow({
                "id": r["id"], "name": r.get("name", ""), "type": r.get("rule_type_id", ""),
                "source": "Kibana Alerting",
                "objective": OBJECTIVE_BY_TYPE.get(r.get("rule_type_id"), r.get("rule_type_id", "-")),
                "index_or_dataview": index_or_dv(r),
                "query_or_condition_normalized": norm_query(r)[:300],
                "schedule_interval": (r.get("schedule") or {}).get("interval", ""),
                "lookback": "{}{}".format(p.get("timeWindowSize", ""), p.get("timeWindowUnit", "")) if p.get("timeWindowSize") is not None else "",
                "threshold": (
                    "{} {}".format(p.get("thresholdComparator", ""), p.get("threshold"))
                    if "threshold" in p else ""
                ),
                "recovery_condition": r.get("notify_when") or "-",
                "fires_90d": hist.get("fires_90d", "ND"),
                "recoveries_90d": hist.get("recoveries_90d", "ND"),
                "avg_time_to_recover": "ND (requer correlacao de eventos individuais)",
                "total_time_active": "ND (requer correlacao de eventos individuais)",
                "flapping_detected": (
                    "possivel" if hist and hist.get("fires_90d", 0) > 20 and hist.get("recoveries_90d", 0) > 20 else "ND"
                ),
                "repeated_same_event": "ND",
                "peak_recurrence_hours": "ND",
                "execution_failures_90d": hist.get("execution_failures_90d", "ND"),
                "connector_failures_90d": "ND",
                "severity": p.get("severity", "-"),
                "grouping": p.get("groupBy") or p.get("termField") or "-",
                "throttle": r.get("throttle") or "-",
                "notification_channel": action_types,
                "owner": "-", "runbook_url": "-",
                "operator_context_present": "Nao (sem acoes)" if not actions else "Parcial",
                "expected_action": "-", "technical_impact": "-", "business_impact": "-",
                "evidence_file": r["_evidence_file"],
            })
    log("Escrito: {} ({} linhas)".format(path, len(rules)))


REC_COLS = [
    "id", "category", "priority", "title", "evidence", "detail",
]


def write_prioritized_backlog():
    path = REPORT_DIR / "prioritized-backlog.csv"
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    sorted_findings = sorted(FINDINGS, key=lambda f: order.get(f.priority, 9))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "category", "priority", "title", "evidence", "detail"])
        for i, finding in enumerate(sorted_findings, 1):
            w.writerow([
                "AUTO-{:03d}".format(i), finding.category, finding.priority,
                finding.title, finding.evidence, finding.detail,
            ])
    log("Escrito: {} ({} achados)".format(path, len(sorted_findings)))
    return sorted_findings


def write_findings_summary(sorted_findings, rules_count):
    path = REPORT_DIR / "findings-summary.md"
    by_prio = {"P0": [], "P1": [], "P2": [], "P3": []}
    for f in sorted_findings:
        by_prio.setdefault(f.priority, []).append(f)

    es_root = load_json("00-es-root.json") or {}
    cluster_name = (es_root.get("version") or {}) and es_root.get("cluster_name", "desconhecido")
    version = (es_root.get("version") or {}).get("number", "desconhecida")

    lines = []
    lines.append("# Relatorio de Achados (gerado localmente por audit_analyze.py)")
    lines.append("")
    lines.append("Gerado em {} (UTC) a partir de `{}`.".format(NOW.strftime("%Y-%m-%d %H:%M:%S"), EVIDENCE_DIR))
    lines.append("")
    lines.append("Cluster: `{}` | Versao Elasticsearch: `{}` | Regras de alerta analisadas: {}".format(
        cluster_name, version, rules_count))
    lines.append("")
    lines.append("**NENHUMA ALTERACAO FOI EXECUTADA NO CLUSTER. Este relatorio e gerado 100% offline, "
                  "a partir de evidencias ja coletadas, sem nenhum acesso a rede.**")
    lines.append("")
    for prio in ("P0", "P1", "P2", "P3"):
        items = by_prio.get(prio, [])
        lines.append("## {} ({} achados)".format(prio, len(items)))
        lines.append("")
        if not items:
            lines.append("_Nenhum achado nesta prioridade._")
            lines.append("")
            continue
        for it in items:
            lines.append("### {}".format(it.title))
            lines.append("- **Categoria:** {}".format(it.category))
            lines.append("- **Evidencia:** {}".format(it.evidence))
            if it.detail:
                lines.append("- **Detalhe:** {}".format(it.detail))
            lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log("Escrito: {}".format(path))


# ---------------------------------------------------------------------------
# Orquestracao
# ---------------------------------------------------------------------------

def main():
    print("=== audit_analyze.py - analise 100% local/offline das evidencias ===")
    print("Evidencia: {}".format(EVIDENCE_DIR))
    print("Relatorio: {}".format(REPORT_DIR))
    print("")

    if not EVIDENCE_DIR.exists():
        log("ERRO: pasta de evidencia '{}' nao existe. Rode audit_collect.py primeiro "
            "(ou aponte AUDIT_EVIDENCE_DIR para a pasta correta).".format(EVIDENCE_DIR))
        sys.exit(2)

    ensure_report_dir()

    analyze_nodes()
    analyze_shard_sizes()
    analyze_transforms()
    analyze_ml_jobs()
    analyze_slm()
    rules, event_log = analyze_alert_rules()
    analyze_saved_objects()

    write_alert_inventory(rules, event_log)
    sorted_findings = write_prioritized_backlog()
    write_findings_summary(sorted_findings, len(rules))

    print("")
    print("Analise concluida. {} achados no total.".format(len(FINDINGS)))
    for prio in ("P0", "P1", "P2", "P3"):
        n = sum(1 for f in FINDINGS if f.priority == prio)
        print("  {}: {}".format(prio, n))
    print("")
    print("Arquivos gerados em: {}".format(REPORT_DIR))
    print("NENHUMA ALTERACAO FOI EXECUTADA NO CLUSTER.")


if __name__ == "__main__":
    main()
