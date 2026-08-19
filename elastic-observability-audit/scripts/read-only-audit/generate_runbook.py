#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_runbook.py
[LOCAL - 100% OFFLINE, SEM REDE, SEM GIT]

Monta um runbook visual autocontido em HTML: um cartao por achado INDIVIDUAL
(uma regra, um watch, um transform, um job de ML, um indice, uma politica ILM,
um no) - nunca um cartao por "familia"/"grupo". Cada cartao traz evidencia
inline, causa raiz, passo a passo de correcao, comando exato (documentado,
nunca executado por este script), validacao e rollback - sem depender de
abrir nenhum outro arquivo.

Duas fontes de achados:
1. MECANICA (a maior parte): le evidence/*.json diretamente (reaproveitando
   os helpers e limiares de audit_analyze.py) e classifica cada objeto
   individualmente por padrao de erro/estado - generico, funciona para
   qualquer cluster, sem nome de cluster/regra/indice fixado no codigo.
2. NARRATIVA (um numero pequeno de achados que exigem verificacao humana de
   conteudo - ex.: duas regras parecidas sao ou nao duplicata real): lidas
   dos blocos "### <ID> — <Titulo>" ja escritos em remediation-plan.md pelo
   skill elastic-audit-deep-analysis, no mesmo formato reutilizado por
   generate_runbook.py desde a primeira versao. Sempre que um achado
   mecanico e um narrativo tratam do mesmo objeto (mesmo ID de regra/watch),
   o achado narrativo linka para a ancora do achado mecanico na mesma
   pagina, em vez de citar outro arquivo.

Uso (depois de rodar audit_analyze.py e a analise profunda que preenche os
documentos da raiz do pacote):

    python generate_runbook.py

Saida em elastic-observability-audit/report/runbook.html por padrao (mesma
pasta de saida do audit_analyze.py - nao versionada). Para apontar para
outra pasta (ex.: multiplos ambientes), use as mesmas variaveis de
audit_analyze.py: AUDIT_EVIDENCE_DIR, AUDIT_REPORT_DIR. Para apontar os
documentos de achados (prioritized-backlog.csv, remediation-plan.md etc.)
para outro lugar, use AUDIT_DOCS_DIR.

IMPORTANTE - sensibilidade dos dados: o HTML gerado reproduz o conteudo real
da evidencia (nomes de regras, indices, mensagens de erro) - o mesmo dado
sensivel que ja existe em evidence/ e nos .md/.csv da raiz do pacote. NAO
comite nem publique publicamente o runbook.html gerado sem a mesma revisao
manual ja exigida para evidence/ (ver README.md, secao "Status deste
repositorio").
"""

import csv
import glob
import html
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import audit_analyze as aa  # noqa: E402  (reaproveita helpers/limiares/EVIDENCE_DIR)

AUDIT_ROOT = SCRIPT_DIR.parents[1]
DOCS_DIR = Path(os.environ.get("AUDIT_DOCS_DIR") or AUDIT_ROOT)
REPORT_DIR = Path(os.environ.get("AUDIT_REPORT_DIR") or (AUDIT_ROOT / "report"))
EVIDENCE_DIR = aa.EVIDENCE_DIR

REMEDIATION_MD = DOCS_DIR / "remediation-plan.md"
EXEC_SUMMARY_MD = DOCS_DIR / "executive-summary.md"
OUT_FILE = REPORT_DIR / "runbook.html"

PRIORITY_ORDER = ["P0", "P1", "P2", "P3"]
PRIORITY_LABEL = {
    "P0": "P0 - Critico / risco de indisponibilidade",
    "P1": "P1 - Ruido grave, saturacao ou falha recorrente",
    "P2": "P2 - Melhoria relevante de eficiencia/confiabilidade",
    "P3": "P3 - Otimizacao ou governanca",
}


# ---------------------------------------------------------------------------
# Helpers genericos
# ---------------------------------------------------------------------------

def esc(text):
    return html.escape("" if text is None else str(text), quote=True)


def inline_md(text):
    text = esc(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`([^`]+?)`", r"<code>\1</code>", text)
    # links internos: [texto](#ancora) - nunca para outro arquivo, so' para outro
    # cartao desta mesma pagina (ver "cross_refs"/siblings).
    text = re.sub(r"\[([^\]]+)\]\(#([a-z0-9-]+)\)", r'<a href="#\2">\1</a>', text)
    return text


def slugify(text):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", str(text).strip().lower())
    return re.sub(r"-+", "-", s).strip("-")


def short_id(full_id, n=8):
    return str(full_id)[:n] if full_id else "?"


def load_json(name, default=None):
    return aa.load_json(name, default)


def load_pages(pattern):
    """Concatena todas as paginas de uma coleta paginada em uma lista unica."""
    out = []
    for _fname, payload in aa.load_json_glob(pattern):
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        if isinstance(data, list):
            out.extend(data)
    return out


def dedupe_by_id(items, id_key="id"):
    seen = set()
    out = []
    for it in items:
        k = it.get(id_key)
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def bytes_of(size_str):
    return aa.human_size_to_bytes(size_str) or 0


class Finding:
    """Um achado individual e autocontido - um objeto (regra/watch/indice/...), nunca um grupo."""

    def __init__(self, fid, priority, category, object_type, title):
        self.id = fid
        self.priority = priority
        self.category = category
        self.object_type = object_type
        self.title = title
        self.evidence = []       # list[str] - cada item ja e uma citacao completa e autocontida
        self.root_cause = ""
        self.impact = ""
        self.steps = []          # list[str] - passo a passo numerado
        self.command = None
        self.execution_location = None
        self.validation_before = None
        self.validation_after = None
        self.rollback = None
        self.risk = None
        self.effort = None
        self.confidence = None
        self.cross_refs = []     # list[(label, anchor_id)] - links internos, nunca outro arquivo
        self.chips = []

    def anchor(self):
        return slugify(self.id)


# ---------------------------------------------------------------------------
# Fontes auxiliares de evidencia
# ---------------------------------------------------------------------------

def load_event_log_index():
    """rule_id -> dict com fires/recoveries/active/failures/executes/first/last (90 dias)."""
    payload = load_json("11-event-log-alert-history-90d.json")
    if not payload:
        return {}
    buckets = payload.get("aggregations", {}).get("by_rule", {}).get("buckets", [])
    out = {}
    for b in buckets:
        actions = {x["key"]: x["doc_count"] for x in b.get("by_action", {}).get("buckets", [])}
        outcomes = {x["key"]: x["doc_count"] for x in b.get("by_outcome", {}).get("buckets", [])}
        out[b["key"]] = {
            "executes": actions.get("execute", 0),
            "fires": actions.get("new-instance", 0),
            "recoveries": actions.get("recovered-instance", 0),
            "active_checks": actions.get("active-instance", 0),
            "failures": outcomes.get("failure", 0),
            "first": (b.get("first_event") or {}).get("value_as_string"),
            "last": (b.get("last_event") or {}).get("value_as_string"),
        }
    return out


def load_dataviews_index():
    out = {}
    for so in load_pages("10-so-dataviews-page*.json"):
        attrs = so.get("attributes", {})
        out[so.get("id")] = {"title": attrs.get("title"), "time_field": attrs.get("timeFieldName")}
    return out


def load_cat_indices():
    return load_json("03-cat-indices.json", []) or []


def find_current_index_for_prefix(cat_indices, missing_index_name):
    """Dado um indice fisico que sumiu (ex.: foo-000058), acha o(s) indice(s)
    atuais com o mesmo prefixo (antes do numero de geracao), se existir."""
    m = re.match(r"^(.*?)-(\d{6})$", missing_index_name)
    prefix = m.group(1) if m else missing_index_name
    matches = [x["index"] for x in cat_indices if x["index"].startswith(prefix + "-") or x["index"] == prefix]
    return sorted(matches)


# ---------------------------------------------------------------------------
# Construtores de achados MECANICOS (genericos, direto da evidencia)
# ---------------------------------------------------------------------------

def classify_alert_error(message, rule, cat_indices, dataviews):
    """Retorna (root_cause:str, steps:list[str], object_ref:str, extra_evidence:list[str])."""
    msg = message or ""

    m = re.search(r"Unknown index \[([^\]]+)\]", msg)
    if m:
        missing = m.group(1)
        current = find_current_index_for_prefix(cat_indices, missing)
        root = ("A consulta referencia o nome fisico do indice `{}`, que nao existe mais "
                "(nome fixo em vez de alias/wildcard).").format(missing)
        steps = [
            "Abrir a regra no Kibana (Stack Management -> Rules, ou GET api/alerting/rule/{}).".format(rule["id"]),
            "Localizar, na query ES|QL/DSL da regra, a referencia literal a `{}`.".format(missing),
        ]
        if current:
            steps.append(
                "Confirmar o(s) indice(s) atual(is) com o mesmo prefixo: {} — trocar a referencia fixa por "
                "um wildcard que cubra qualquer geracao futura (ex.: `{}*`), nao apenas atualizar o numero "
                "manualmente (senao quebra de novo no proximo rollover).".format(
                    ", ".join("`{}`".format(c) for c in current), missing.rsplit("-", 1)[0]))
        else:
            steps.append(
                "Nao foi encontrado nenhum indice atual com o mesmo prefixo em `evidence/03-cat-indices.json` — "
                "confirmar com o time de dados se o indice de origem foi renomeado, migrado ou descontinuado "
                "antes de apenas trocar por um wildcard.")
        steps.append("Salvar e confirmar que `execution_status.status` volta a `ok` na proxima execucao.")
        extra = (["Indice(s) atual(is) com o mesmo prefixo: " + ", ".join(current)] if current
                  else ["Nenhum indice atual encontrado com o prefixo `{}` em evidence/03-cat-indices.json — "
                        "possível migração/descontinuação, confirmar com o owner."
                        .format(missing.rsplit("-", 1)[0])])
        return root, steps, "índice `{}`".format(missing), extra

    m = re.search(r"Data view with ID ([a-zA-Z0-9-]+) no longer contains a time field", msg)
    if m:
        dv_id = m.group(1)
        dv = dataviews.get(dv_id, {})
        title = dv.get("title") or "(data view não encontrada em evidence/10-so-dataviews-*.json)"
        tf = dv.get("time_field") or "(nenhum configurado)"
        root = ("A data view `{}` (id `{}`) está configurada com o campo de tempo `{}`, mas esse campo "
                 "não existe mais no mapping atual do índice/padrão de origem.").format(title, dv_id, tf)
        steps = [
            "Abrir Kibana -> Stack Management -> Data Views -> localizar a data view `{}` (id `{}`).".format(title, dv_id),
            "Rodar `GET {}/_mapping` (Dev Tools) para ver os campos de data/hora disponíveis hoje no índice de origem.".format(title),
            "Atualizar o campo de tempo da data view (`timeFieldName`) para o campo correto encontrado no mapping atual — "
            "ou, se o índice de origem mudou de nome/padrão, atualizar o próprio padrão de índice da data view.",
            "Salvar e confirmar que a regra volta a `execution_status.status = ok` na próxima execução.",
        ]
        return root, steps, "data view `{}`".format(title), ["Time field configurado hoje: `{}`".format(tf)]

    m = re.search(r"Enrich field \[([^\]]+)\] not found in enrich policy \[([^\]]+)\], did you mean any of \[([^\]]+)", msg)
    if m:
        wrong_field, policy, suggestions = m.group(1), m.group(2), m.group(3)
        root = ("A regra referencia o campo `{}` da enrich policy `{}`, mas esse campo não existe nela — "
                 "o próprio Elasticsearch já sugere o nome correto na mensagem de erro.").format(wrong_field, policy)
        steps = [
            "Abrir a regra no Kibana e localizar a referência a `{}` na consulta/enrich processor.".format(wrong_field),
            "Trocar por um dos campos sugeridos pelo próprio erro: {}.".format(suggestions),
            "Confirmar contra `GET _enrich/policy/{}` que o campo escolhido é o correto para o caso de uso.".format(policy),
            "Salvar e confirmar `execution_status.status = ok` na próxima execução.",
        ]
        return root, steps, "enrich policy `{}`".format(policy), ["Sugestão literal do Elasticsearch: {}".format(suggestions)]

    m = re.search(r"No known job with id '([^']+)'", msg)
    if m:
        job_id = m.group(1)
        root = "A regra referencia o job de ML `{}`, que não existe na lista atual de jobs.".format(job_id)
        steps = [
            "Confirmar com `GET _ml/anomaly_detectors/{}` que o job realmente não existe mais (não apenas fechado).".format(job_id),
            "Se o job foi intencionalmente removido: a regra é órfã — candidata à exclusão (ver critério de observação abaixo).",
            "Se o job deveria existir: recriar o job de ML a partir de um backup de configuração antes de reativar a regra.",
        ]
        return root, steps, "job de ML `{}`".format(job_id), []

    m = re.search(r"Transform with id \[([^\]]+)\] could not be found", msg)
    if m:
        tid = m.group(1)
        root = "A regra referencia o transform `{}`, que não existe na lista atual de transforms.".format(tid)
        steps = [
            "Confirmar com `GET _transform/{}` que o transform realmente não existe mais.".format(tid),
            "Se foi intencionalmente removido: a regra é órfã — candidata à exclusão (ver critério de observação abaixo).",
            "Se deveria existir: recriar o transform a partir de um backup de configuração antes de reativar a regra.",
        ]
        return root, steps, "transform `{}`".format(tid), []

    m = re.search(r"no such index \[([^\]]+)\]", msg)
    if m:
        missing = m.group(1)
        root = "A regra referencia o índice `{}` (provavelmente um índice de enrich temporário) que não existe mais.".format(missing)
        steps = [
            "Confirmar se `{}` era um índice de enrich temporário expirado (padrão `.enrich-*`) ou um índice de dados real.".format(missing),
            "Se for de enrich: verificar se a enrich policy correspondente ainda existe e foi re-executada (`GET _enrich/policy`).",
            "Se for um índice de dados: confirmar com o time de dados se foi renomeado/excluído intencionalmente.",
        ]
        return root, steps, "índice `{}`".format(missing), []

    if "Request timed out" in msg or "timed out" in msg.lower():
        root = "A execução da regra excede o tempo limite configurado — consulta cara e/ou nó de origem saturado."
        steps = [
            "Extrair `params.esQuery`/`params.esqlQuery`/`params.index` da regra (`GET api/alerting/rule/{}`) e medir o "
            "tempo de resposta da mesma consulta manualmente no Dev Tools.".format(rule["id"]),
            "Cruzar o índice/tier de origem com `evidence/02-nodes-breakers.json`/`02-cat-nodes.json` — se o tier estiver "
            "saturado (CPU/RAM/circuit breaker), tratar como sintoma do problema de capacidade, não como bug isolado desta regra.",
            "Se a consulta for genuinamente cara independente do tier: reduzir `lookback`, adicionar filtro mais seletivo, "
            "ou aumentar o intervalo de execução para um valor compatível com o tempo real de resposta.",
        ]
        return root, steps, "timeout de execução", []

    m = re.search(r"Cannot read properties of undefined \(reading '([^']+)'\)", msg)
    if m:
        prop = m.group(1)
        root = ("Erro de execução do motor de regra ao acessar a propriedade `{}` de um objeto indefinido — "
                 "geralmente indica configuração incompleta/corrompida da regra (ex.: filtro de tempo ausente).").format(prop)
        steps = [
            "Abrir `GET api/alerting/rule/{}` e revisar `params` por completo em busca de um campo ausente/nulo "
            "relacionado a `{}`.".format(rule["id"], prop),
            "Comparar com uma regra do mesmo tipo que funciona corretamente para identificar o campo faltante.",
            "Corrigir o campo ausente e salvar; confirmar `execution_status.status = ok` na próxima execução.",
        ]
        return root, steps, "configuração da regra", []

    root = "Causa raiz não reconhecida por nenhum padrão automático — revisar a mensagem de erro manualmente."
    steps = [
        "Abrir `GET api/alerting/rule/{}` e ler `execution_status.error` na íntegra.".format(rule["id"]),
        "Reproduzir a consulta/condição manualmente no Dev Tools para isolar a causa.",
    ]
    return root, steps, "erro não classificado", []


def build_alert_findings(rules, event_index, cat_indices, dataviews):
    findings = []
    # mensagem de erro exata -> lista de (rule_id, enabled) - para cross-referenciar
    # regras irmas com a MESMA causa raiz (ativas ou desabilitadas, misturadas).
    error_groups = {}
    for r in rules:
        if (r.get("execution_status") or {}).get("status") == "error":
            msg = (r.get("execution_status") or {}).get("error", {}).get("message", "")
            error_groups.setdefault(msg, []).append((r["id"], bool(r.get("enabled"))))

    def sibling_links(msg, rid):
        siblings = [(s, s_enabled) for s, s_enabled in error_groups.get(msg, []) if s != rid]
        if not siblings:
            return None
        links = ["[{sid}](#{anchor})".format(sid=sid, anchor=slugify(sid)) for sid in
                 ("ALR-{}-{}".format(short_id(s), "ERR" if s_enabled else "DEAD") for s, s_enabled in siblings)]
        return "Mesma causa raiz exata afeta {} outra(s) regra(s) neste runbook: {}.".format(len(siblings), ", ".join(links))

    for r in rules:
        rid = r["id"]
        name = r.get("name", "")
        enabled = r.get("enabled")
        rtype = r.get("rule_type_id", "")
        status = (r.get("execution_status") or {}).get("status")
        ev90 = event_index.get(rid, {})

        # 1) Regra ativa em erro AGORA
        if enabled and status == "error":
            msg = r.get("execution_status", {}).get("error", {}).get("message", "")
            root, steps, obj_ref, extra = classify_alert_error(msg, r, cat_indices, dataviews)
            f = Finding("ALR-{}-ERR".format(short_id(rid)), "P1", "Alerting", "regra de alerting (Kibana)",
                        "{} — em erro agora".format(name))
            f.evidence = [
                "`evidence/09-kibana-alerting-rules-page*.json`, id `{}`, tipo `{}`, agendamento `{}`.".format(
                    rid, rtype or "?", (r.get("schedule") or {}).get("interval", "?")),
                "Mensagem de erro literal: `{}`".format(msg.split("\n")[0][:300]),
            ] + extra
            sib_note = sibling_links(msg, rid)
            if sib_note:
                f.evidence.append(sib_note)
            if ev90.get("executes"):
                f.evidence.append(
                    "Histórico de 90 dias: {} execuções, {} com falha ({:.0f}% de falha), última execução "
                    "registrada em {}.".format(
                        ev90["executes"], ev90["failures"],
                        100.0 * ev90["failures"] / ev90["executes"] if ev90["executes"] else 0, ev90.get("last", "?")))
            f.root_cause = root
            f.impact = "Regra parece \"ativa\" no inventário, mas não está de fato monitorando a condição real hoje — cobertura efetivamente zero para este caso de uso."
            f.steps = steps
            f.execution_location = "[KIBANA UI ou KIBANA DEV TOOLS] — IMPLEMENTAÇÃO FUTURA"
            f.validation_before = "GET api/alerting/rule/{} — confirmar execution_status.status atual (`error`).".format(rid)
            f.validation_after = "GET api/alerting/rule/{} — confirmar execution_status.status = `ok` por pelo menos 3 execuções consecutivas.".format(rid)
            f.rollback = "Restaurar o JSON original da regra (params/query) salvo antes da mudança via PUT api/alerting/rule/{}.".format(rid)
            f.risk = "Baixo (correção de referência quebrada não muda a lógica de negócio da regra)."
            f.effort = "Baixo"
            f.confidence = "Alta — causa raiz extraída diretamente da mensagem de erro do motor de execução."
            findings.append(f)

        # 2) Regra desabilitada, ultima execucao ja em erro apontando pra recurso morto
        elif not enabled and status == "error":
            msg = r.get("execution_status", {}).get("error", {}).get("message", "")
            root, steps, obj_ref, extra = classify_alert_error(msg, r, cat_indices, dataviews)
            f = Finding("ALR-{}-DEAD".format(short_id(rid)), "P3", "Governança", "regra de alerting (Kibana)",
                        "{} — desabilitada, referência morta".format(name))
            f.evidence = [
                "`evidence/09-kibana-alerting-rules-page*.json`, id `{}`, tipo `{}`.".format(rid, rtype or "?"),
                "Mensagem de erro da última execução (antes de ser desabilitada): `{}`".format(msg.split("\n")[0][:300]),
            ] + extra
            sib_note = sibling_links(msg, rid)
            if sib_note:
                f.evidence.append(sib_note)
            f.root_cause = root
            f.impact = "Não gera ruído hoje (desabilitada), mas é um objeto morto confirmado no inventário — se alguém reabilitar sem corrigir, falha imediatamente."
            f.steps = [
                "Confirmar (passo 1 de `{}` acima) que o recurso referenciado realmente não existe mais.".format(root.split(".")[0]),
            ] + steps[:1] + [
                "Se confirmado morto: aguardar 30 dias de observação sem que ninguém peça a reativação, então excluir via `DELETE api/alerting/rule/{}`.".format(rid),
                "Se alguém precisar dela: corrigir a referência (não apenas reabilitar) antes de qualquer reativação.",
            ]
            f.execution_location = "[KIBANA UI ou KIBANA DEV TOOLS] — IMPLEMENTAÇÃO FUTURA"
            f.validation_before = "GET api/alerting/rule/{} — confirmar enabled=false e a mensagem de erro acima.".format(rid)
            f.validation_after = "GET api/alerting/rules/_find?search=<nome> — deve retornar 0 resultados após a exclusão."
            f.rollback = "Recriar a regra a partir do JSON salvo em current-config-backups/ (POST api/alerting/rule)."
            f.risk = "Baixo (regra já desabilitada; exclusão remove só um objeto morto)."
            f.effort = "Baixo, com período de observação de 30 dias antes da exclusão definitiva."
            f.confidence = "Média-Alta (referência quebrada confirmada; exclusão definitiva ainda exige confirmação humana)."
            findings.append(f)

        # 3) Regra ativa sem nenhuma acao/notificacao (exceto tipos exentos, ex. SLO burn-rate)
        if enabled and not r.get("actions") and rtype not in aa.CFG["NO_ACTION_EXEMPT_RULE_TYPES"]:
            f = Finding("ALR-{}-NOACT".format(short_id(rid)), "P2", "Alerting", "regra de alerting (Kibana)",
                        "{} — sem nenhuma ação de notificação".format(name))
            f.evidence = [
                "`evidence/09-kibana-alerting-rules-page*.json`, id `{}`, tipo `{}`, enabled=true, actions=[].".format(rid, rtype or "?"),
            ]
            f.root_cause = "A regra está habilitada e pode disparar, mas não tem nenhum conector/ação configurado — se a condição for satisfeita, ninguém fora da UI do Kibana é notificado."
            f.impact = "Falha silenciosa por desenho: a condição pode estar ativa agora mesmo sem que ninguém saiba."
            f.steps = [
                "Identificar o owner/time responsável pela regra `{}` (via tags, se presentes, ou pelo nome/domínio de negócio).".format(name),
                "Definir o canal de notificação apropriado (e-mail, Slack/webhook, índice de auditoria).",
                "Abrir a regra no Kibana -> aba Actions -> adicionar a ação com o conector escolhido.",
                "Testar a ação (Kibana permite \"Run connector\" isoladamente) antes de salvar em produção.",
            ]
            f.execution_location = "[KIBANA UI] — IMPLEMENTAÇÃO FUTURA"
            f.validation_before = "GET api/alerting/rule/{} — confirmar actions=[].".format(rid)
            f.validation_after = "GET api/alerting/rule/{} — confirmar pelo menos 1 ação presente e testada.".format(rid)
            f.rollback = "Remover a ação adicionada (PUT api/alerting/rule/{} com actions=[]).".format(rid)
            f.risk = "Baixo."
            f.effort = "Baixo — depende só de identificar o owner/canal."
            f.confidence = "Alta."
            findings.append(f)

        # 4) Alarme travado: active-instance>0, new-instance=0 (nunca recuperou na janela), exceto SLO burn-rate
        active_i = ev90.get("active_checks", 0)
        new_i = ev90.get("fires", 0)
        if enabled and active_i > 0 and new_i == 0 and rtype != "slo.rules.burnRate":
            f = Finding("ALR-{}-STUCK".format(short_id(rid)), "P1", "Alerting", "regra de alerting (Kibana)",
                        "{} — alarme travado, sem recuperação em 90 dias".format(name))
            f.evidence = [
                "`evidence/11-event-log-alert-history-90d.json`: {} checagens em estado ativo, 0 recuperações no período. "
                "Primeiro evento da janela: {}. Último evento: {}.".format(active_i, ev90.get("first", "?"), ev90.get("last", "?")),
                "Tipo de regra: `{}` (não é burn-rate de SLO, onde ficar ativo por longos períodos é esperado).".format(rtype or "?"),
            ]
            f.root_cause = ("A condição da regra está satisfeita continuamente desde antes do início da janela de 90 dias — "
                             "ou a condição de negócio realmente está anormal há esse tempo, ou o threshold/condição de "
                             "recuperação está mal calibrado.")
            f.impact = "O alerta perdeu função de sinal: uma checagem em estado ativo pela milésima vez não distingue \"problema novo\" de \"problema antigo\"."
            f.steps = [
                "Identificar o owner do processo de negócio monitorado por `{}`.".format(name),
                "Confirmar com o owner se a condição de origem ainda é real e ativa hoje.",
                "Se for um problema real não resolvido: tratar como incidente aberto (não é mais só um alerta de monitoramento).",
                "Se for miscalibração: ajustar o threshold/recovery_condition da regra com o owner e validar que ela volta a alternar active/recovered normalmente.",
            ]
            f.execution_location = "[KIBANA UI] — IMPLEMENTAÇÃO FUTURA, somente após confirmação do owner."
            f.validation_before = "GET api/alerting/rule/{} — registrar threshold/condição atual.".format(rid)
            f.validation_after = "Repetir a consulta ao event log 90 dias depois — confirmar que a regra passou a gerar eventos recovered-instance."
            f.rollback = "Reverter threshold/recovery_condition para o valor salvo em current-config-backups/."
            f.risk = "Baixo para investigar; alto para alterar threshold sem entender a causa (pode mascarar um problema real)."
            f.effort = "Baixo (investigação) a médio (se precisar recalibrar)."
            f.confidence = "Alta no padrão de dados; baixa na causa raiz de negócio (não verificável só com dados técnicos)."
            findings.append(f)

    return findings, error_groups


def build_monitoring_coverage_findings(rules, event_index):
    findings = []
    mon_rules = [r for r in rules if (r.get("rule_type_id") or "").startswith("monitoring_alert")]
    for r in mon_rules:
        rid = r["id"]
        name = r.get("name", "")
        enabled = r.get("enabled")
        n_actions = len(r.get("actions") or [])
        ev90 = event_index.get(rid, {})

        if not enabled:
            f = Finding("MON-{}".format(short_id(rid)), "P0", "Observabilidade", "regra nativa de saúde do cluster",
                        "Alerta nativo \"{}\" está DESABILITADO".format(name))
            f.evidence = [
                "`evidence/09-kibana-alerting-rules-page*.json`, id `{}`, tipo `{}`, enabled=false.".format(rid, r.get("rule_type_id")),
            ]
            f.root_cause = "Este é um alerta nativo do Elastic Stack Monitoring, desabilitado — a rede de segurança automática para esta condição de saúde do cluster está desligada."
            f.impact = "Se esta condição específica de saúde do cluster degradar, ninguém é notificado automaticamente."
            f.steps = [
                "Abrir Kibana -> Stack Management -> Rules -> localizar \"{}\".".format(name),
                "Confirmar com o time se a desativação foi intencional (ex.: ruído conhecido) ou um esquecimento.",
                "Se não houver razão documentada para mantê-la desligada: habilitar e configurar/confirmar o canal de notificação da ação.",
            ]
            f.execution_location = "[KIBANA UI] — IMPLEMENTAÇÃO FUTURA"
            f.validation_before = "GET api/alerting/rule/{} — confirmar enabled=false.".format(rid)
            f.validation_after = "GET api/alerting/rule/{} — confirmar enabled=true e execution_status=ok após a primeira execução.".format(rid)
            f.rollback = "PUT api/alerting/rule/{} com enabled=false.".format(rid)
            f.risk = "Baixo."
            f.effort = "Trivial."
            f.confidence = "Alta."
            findings.append(f)
        else:
            flap_note = ""
            priority = "P3"
            if ev90.get("fires", 0) > 20 and ev90.get("recoveries", 0) > 20:
                priority = "P1"
                flap_note = (" Disparou {} vezes e recuperou {} vezes em 90 dias (~{:.0f}x/dia) — instabilidade "
                             "sustentada da condição monitorada, não um evento isolado."
                             ).format(ev90["fires"], ev90["recoveries"], ev90["fires"] / 90.0)
            f = Finding("MON-{}".format(short_id(rid)), priority, "Observabilidade", "regra nativa de saúde do cluster",
                        "Confirmar canal de notificação do alerta nativo \"{}\"".format(name))
            f.evidence = [
                "`evidence/09-kibana-alerting-rules-page*.json`, id `{}`, enabled=true, {} ação(ões) configurada(s).".format(rid, n_actions),
            ]
            if ev90.get("executes"):
                f.evidence.append(
                    "Histórico de 90 dias: {} disparos, {} recuperações.{}".format(
                        ev90.get("fires", 0), ev90.get("recoveries", 0), flap_note))
            f.root_cause = "A regra existe e está habilitada, mas não é possível confirmar apenas com a evidência coletada se o canal de notificação da sua ação é efetivamente observado por um humano/processo de oncall."
            f.impact = "Um alerta habilitado cujo canal ninguém olha é, na prática, o mesmo que não ter alerta."
            f.steps = [
                "GET api/alerting/rule/{} e extrair o conector usado em `actions[].id`.".format(rid),
                "Cruzar com `evidence/09-kibana-connectors.json` para identificar o tipo/destino do conector.",
                "Confirmar com o time responsável que esse canal é monitorado ativamente (não é uma caixa de e-mail morta, por exemplo).",
            ]
            f.execution_location = "[KIBANA UI ou KIBANA DEV TOOLS] — verificação, sem mudança de configuração necessária."
            f.validation_before = "N/A (é uma verificação)."
            f.validation_after = "Confirmação documentada de quem monitora o canal."
            f.rollback = "N/A."
            f.risk = "Nenhum (verificação)."
            f.effort = "Trivial."
            f.confidence = "Alta."
            findings.append(f)
    return findings


def build_transform_findings():
    findings = []
    payload = load_json("07-transforms-stats.json", {"transforms": []})
    for t in payload.get("transforms", []):
        if t.get("state") not in ("failed",) and not (t.get("health") or {}).get("status") == "red":
            if t.get("state") != "failed":
                continue
        reason = t.get("reason", "") or ((t.get("health") or {}).get("issues") or [{}])[0].get("issue", "")
        tid = t.get("id")
        f = Finding("XFM-{}".format(slugify(tid)), "P1", "Resiliência", "transform",
                    "Transform \"{}\" em falha ativa".format(tid))
        f.evidence = ["`evidence/07-transforms-stats.json`, id `{}`, state=`{}`.".format(tid, t.get("state")),
                      "Mensagem literal: `{}`".format(str(reason)[:400])]
        low = str(reason).lower()
        if "increase heap size" in low or "insufficient memory" in low:
            f.root_cause = "Falha por memória insuficiente durante o pivot — mensagem do próprio Elasticsearch recomenda aumentar o heap dos data nodes envolvidos."
            f.steps = [
                "Identificar em qual(is) nó(s)/tier o transform executa (`node.name` em `evidence/07-transforms-stats.json`).",
                "Cruzar com `evidence/02-nodes-breakers.json`/`02-cat-nodes.json` — se o tier estiver com heap pequeno/CPU saturada, tratar como consequência do mesmo problema (não como bug isolado do transform).",
                "Após corrigir a capacidade do tier, reiniciar o transform: `POST _transform/{}/_start`.".format(tid),
            ]
            f.priority = "P1"
        elif "flood-stage watermark" in low or "read-only-allow-delete" in low:
            f.root_cause = "Falha ao criar checkpoint porque um índice interno do transform foi bloqueado por watermark de disco cheio (`flood-stage`)."
            f.steps = [
                "Confirmar o espaço em disco atual do cluster (Elastic Cloud Console -> Deployment -> Storage).",
                "Se já normalizado: remover o bloqueio `read-only-allow-delete` do índice interno afetado (`PUT <índice>/_settings` com `index.blocks.read_only_allow_delete: null`).",
                "Reiniciar o transform: `POST _transform/{}/_start`.".format(tid),
            ]
        elif "index closed" in low or "forbidden" in low:
            m = re.search(r"index \[([^\]]+)\]", str(reason))
            closed_idx = m.group(1) if m else "?"
            f.root_cause = "O índice de origem `{}` está fechado (`FORBIDDEN/4/index closed`) — índices fechados não aparecem no inventário padrão `_cat/indices` sem `expand_wildcards=all`.".format(closed_idx)
            f.steps = [
                "Confirmar com o time de dados se o fechamento de `{}` foi intencional (arquivamento) ou acidental.".format(closed_idx),
                "Se intencional: parar/reapontar o transform em vez de deixá-lo falhando indefinidamente (`POST _transform/{}/_stop`).".format(tid),
                "Se acidental: reabrir o índice (`POST {}/_open`) e reiniciar o transform.".format(closed_idx),
            ]
            f.evidence.append("Este índice fechado NÃO aparece em `evidence/03-cat-indices.json` (coleta sem `?expand_wildcards=all`) — confirma lacuna de inventário de índices fechados.")
        elif "could not be found" in low or "resource_not_found" in low:
            f.root_cause = "O transform aponta para um recurso (índice/pipeline) que não existe mais."
            f.steps = ["Confirmar via `GET _transform/{}` a definição completa e qual recurso está ausente.".format(tid),
                       "Corrigir a referência ou, se o transform for obsoleto, excluí-lo (`DELETE _transform/{}?force=true`)." .format(tid)]
        elif "persist" in low or "more than 10 failures" in low:
            f.root_cause = "Falhas repetidas ao persistir estatísticas internas — geralmente indica instabilidade do nó ou versão obsoleta do transform."
            f.steps = [
                "Verificar se existe uma versão mais nova do mesmo transform de sistema já saudável (`GET _transform` filtrando por prefixo similar).",
                "Se existir versão mais nova saudável: este é candidato à exclusão (versão obsoleta superada) — `DELETE _transform/{}?force=true`.".format(tid),
                "Se não existir: `POST _transform/{}/_stop` seguido de `_start` para forçar reset de estado; se persistir, escalar para o time de dados.".format(tid),
            ]
        else:
            f.root_cause = "Causa raiz não classificada automaticamente — revisar a mensagem de erro completa."
            f.steps = ["GET _transform/{}/_stats e ler `reason`/`health.issues` na íntegra.".format(tid)]
        f.impact = "Transform não produz dados atualizados no índice de destino desde a falha."
        f.execution_location = "[KIBANA DEV TOOLS] — IMPLEMENTAÇÃO FUTURA"
        f.validation_before = "GET _transform/{}/_stats — registrar state=failed.".format(tid)
        f.validation_after = "GET _transform/{}/_stats — confirmar state=started/indexing e checkpoint avançando.".format(tid)
        f.rollback = "POST _transform/{}/_stop caso a correção piore o estado.".format(tid)
        f.risk = "Baixo a médio, conforme a causa."
        f.effort = "Baixo a médio."
        f.confidence = "Alta (mensagem de erro explícita do Elasticsearch)."
        findings.append(f)
    return findings


def build_ml_job_findings():
    findings = []
    stats = load_json("07-ml-jobs-stats.json", {"jobs": []})
    stale_days = aa.CFG["ML_STALE_DAYS"]
    for j in stats.get("jobs", []):
        dc = j.get("data_counts", {})
        last_ts = dc.get("latest_record_timestamp")
        if last_ts is None:
            continue
        last_dt = aa.parse_es_timestamp_ms(last_ts)
        days = aa.days_since(last_dt)
        if days is None or days < stale_days:
            continue
        job_id = j["job_id"]
        state = j.get("state")
        model_bytes = (j.get("model_size_stats") or {}).get("model_bytes", 0)
        f = Finding("MLJ-{}".format(slugify(job_id)), "P2" if state == "opened" else "P3", "FinOps/Limpeza", "job de Machine Learning",
                    "Job de ML \"{}\" ({}) sem dado novo há {} dias".format(job_id, state, days))
        f.evidence = [
            "`evidence/07-ml-jobs-stats.json`: job_id=`{}`, state=`{}`, latest_record_timestamp={} ({} dias atrás).".format(
                job_id, state, last_ts, days),
            "Memória do modelo carregada agora: {:.1f} MB (`model_size_stats.model_bytes`).".format(model_bytes / 1024**2),
        ]
        if state == "opened":
            f.root_cause = "Job permanece \"opened\" (modelo carregado em memória no nó de ML) apesar de não processar dados novos há {} dias — consome recursos sem função ativa.".format(days)
            f.impact = "Ocupa {:.1f} MB de memória no nó de ML sem produzir valor.".format(model_bytes / 1024**2)
            f.steps = [
                "Confirmar com o owner (se identificável pelo nome do job) que o datafeed/fonte de dados foi descontinuada intencionalmente.",
                "Fechar o job (reversível, não apaga resultados históricos): `POST _ml/anomaly_detectors/{}/_close`.".format(job_id),
                "Se confirmado definitivamente obsoleto após um período de observação: considerar exclusão (`DELETE _ml/anomaly_detectors/{}`).".format(job_id),
            ]
        else:
            f.root_cause = "Job já está fechado e sem dado novo há {} dias — não consome memória ativa, mas é um objeto morto no inventário.".format(days)
            f.impact = "Nenhum (já fechado) — apenas poluição de inventário."
            f.steps = [
                "Confirmar com o owner que pode ser excluído definitivamente.",
                "Excluir: `DELETE _ml/anomaly_detectors/{}`.".format(job_id),
            ]
        f.execution_location = "[KIBANA DEV TOOLS] — IMPLEMENTAÇÃO FUTURA"
        f.validation_before = "GET _ml/anomaly_detectors/{}/_stats — registrar state e model_bytes atuais.".format(job_id)
        f.validation_after = "GET _ml/anomaly_detectors/{}/_stats — confirmar state=closed (ou 404 se excluído) e queda de memória no nó de ML.".format(job_id)
        f.rollback = "POST _ml/anomaly_detectors/{}/_open (se apenas fechado) — se excluído, recriar a partir do backup de configuração.".format(job_id)
        f.risk = "Baixo."
        f.effort = "Baixo."
        f.confidence = "Alta no padrão de dados (nenhum registro novo há {}+ dias); média na decisão de exclusão definitiva (requer confirmação humana de que não será reativado).".format(days)
        findings.append(f)
    return findings


def build_watch_findings():
    findings = []
    payload = load_json("08-watches-query.json", {"watches": []})
    for w in payload.get("watches", []):
        wid = w["_id"]
        watch = w.get("watch", {})
        status = w.get("status", {})
        wname = watch.get("metadata", {}).get("name", wid)
        active = status.get("state", {}).get("active")
        acts = status.get("actions", {})
        for act_name, act in acts.items():
            le = act.get("last_execution", {})
            if le.get("successful") is False:
                f = Finding("WCH-{}".format(slugify(wid)), "P1" if active else "P3", "Alerting", "watch (X-Pack legado)",
                            "Watch \"{}\" — ação \"{}\" falhando".format(wname, act_name))
                f.evidence = [
                    "`evidence/08-watches-query.json`, id `{}`, active={}, agendamento `{}`.".format(
                        wid, active, watch.get("trigger", {}).get("schedule", {})),
                    "Última execução da ação `{}`: timestamp {}, successful=false.".format(act_name, le.get("timestamp")),
                    "Última checagem do watch: {}.".format(status.get("last_checked")),
                ]
                cond = watch.get("condition", {})
                inp = watch.get("input", {})
                if "always" in cond and "none" in inp:
                    f.root_cause = ("Watch dispara incondicionalmente no horário agendado (sem consulta/condição real, "
                                     "`input:none`+`condition:always`) e sua ação de notificação está falhando — funciona "
                                     "como um lembrete/relatório agendado, não uma detecção condicional.")
                else:
                    idx = (inp.get("search", {}).get("request", {}) or {}).get("indices")
                    f.root_cause = "Watch consulta {} e sua ação de notificação está falhando na entrega.".format(
                        "o(s) índice(s) `{}`".format(idx) if idx else "uma fonte de dados própria")
                f.impact = "Notificação/relatório de negócio não está sendo entregue desde pelo menos a última execução registrada."
                f.steps = [
                    "Rodar `GET _watcher/watch/{}` para o estado completo mais recente (a API de listagem em lote não expõe a razão detalhada da falha).".format(wid),
                    "Se disponível, consultar `.watcher-history-*` filtrando por `watch_id: {}` para a mensagem de erro completa da ação.".format(wid),
                    "Verificar o conector da ação `{}` (webhook/e-mail) — testar entrega manualmente fora do watch, se possível.".format(act_name),
                    "Corrigir o conector/destino e confirmar `last_execution.successful=true` na próxima execução.",
                ]
                f.execution_location = "[KIBANA DEV TOOLS / ELASTIC CLOUD CONSOLE] — IMPLEMENTAÇÃO FUTURA"
                f.validation_before = "GET _watcher/watch/{} — registrar status.actions.{}.last_execution.".format(wid, act_name)
                f.validation_after = "Repetir a mesma consulta após a correção — confirmar successful=true."
                f.rollback = "Reverter a configuração da ação para o JSON original salvo antes da mudança (PUT _watcher/watch/{}).".format(wid)
                f.risk = "Baixo."
                f.effort = "Médio (causa raiz não exposta diretamente pela API de consulta de watches em lote)."
                f.confidence = "Média (padrão de falha confirmado; causa raiz exata requer .watcher-history-* ou consulta individual)."
                findings.append(f)
    return findings


def build_oversized_index_findings(cat_indices):
    findings = []
    explain = load_json("04-ilm-explain-managed.json", {"indices": {}})
    policies = load_json("04-ilm-policies.json", {})
    idx_info = explain.get("indices", {})
    ratio_threshold = aa.CFG["SHARD_OVERSIZE_RATIO"]

    for x in cat_indices:
        name = x["index"]
        info = idx_info.get(name)
        if not info:
            continue
        policy_name = info.get("policy")
        policy = policies.get(policy_name, {})
        phases = (policy.get("policy") or {}).get("phases", {})
        hot_actions = (phases.get("hot") or {}).get("actions", {})
        max_size = (hot_actions.get("rollover") or {}).get("max_primary_shard_size")
        if not max_size:
            continue
        limit_bytes = bytes_of(max_size)
        real_bytes = bytes_of(x.get("store.size"))
        if not limit_bytes or real_bytes <= limit_bytes * ratio_threshold:
            continue
        f = Finding("IDX-{}".format(slugify(name)), "P1", "Performance", "índice",
                    "Índice \"{}\" {:.1f}x maior que o limite de rollover da política ILM".format(name, real_bytes / limit_bytes))
        f.evidence = [
            "`evidence/03-cat-indices.json`: `{}`, store.size=`{}`.".format(name, x.get("store.size")),
            "`evidence/04-ilm-policies.json`: política `{}`, max_primary_shard_size configurado=`{}`.".format(policy_name, max_size),
            "`evidence/04-ilm-explain-managed.json`: fase atual=`{}`, ação atual=`{}`.".format(info.get("phase"), info.get("action")),
        ]
        if info.get("action") in ("complete", "rollover"):
            f.root_cause = ("O rollover JÁ ocorreu (ação `{}`), só que tarde — o índice cresceu além do limite antes do "
                             "ILM avaliar/disparar a condição. Não é um ILM quebrado, é um limiar/frequência de poll "
                             "insuficiente para o volume de escrita real deste índice.").format(info.get("action"))
            f.steps = [
                "Confirmar a frequência de poll do ILM (`indices.lifecycle.poll_interval`, padrão 10 minutos) contra a taxa de ingestão real deste índice.",
                "Reduzir `max_primary_shard_size` na política `{}` para um valor que dê margem de segurança à taxa de ingestão atual, ou adicionar uma condição adicional de rollover por `max_age`/`max_docs`.".format(policy_name),
                "Validar em 2-3 rollovers subsequentes que o tamanho do shard fica dentro do novo limite.",
            ]
        else:
            f.root_cause = "ILM não avançou para o rollover apesar do índice já estar acima do limite configurado."
            f.steps = [
                "Rodar `GET {}/_ilm/explain` para ver `step_info` e identificar se há um erro bloqueando o rollover.".format(name),
                "Corrigir o erro reportado (ex.: permissão, alias ausente) antes de qualquer ajuste de limiar.",
            ]
        f.impact = "Shards muito maiores que o planejado aumentam tempo de recuperação (recovery) e reduzem paralelismo de busca."
        f.execution_location = "[ELASTIC CLOUD CONSOLE / KIBANA DEV TOOLS] — IMPLEMENTAÇÃO FUTURA"
        f.validation_before = "GET {}/_ilm/explain — registrar phase/action/step atuais.".format(name)
        f.validation_after = "Após o próximo rollover, confirmar que o novo índice de geração fica dentro do limite revisado."
        f.rollback = "Reverter max_primary_shard_size da política `{}` para o valor original.".format(policy_name)
        f.risk = "Baixo (mudança de limiar não afeta dados já indexados até o próximo rollover)."
        f.effort = "Médio (requer alguns rollovers para validar)."
        f.confidence = "Alta."
        findings.append(f)
    return findings


def build_ilm_warm_gap_findings(cat_indices, min_gb=1.0):
    findings = []
    explain = load_json("04-ilm-explain-managed.json", {"indices": {}})
    policies = load_json("04-ilm-policies.json", {})
    idx_info = explain.get("indices", {})
    size_by_idx = {x["index"]: bytes_of(x.get("store.size")) for x in cat_indices}

    agg = {}
    for iname, info in idx_info.items():
        pol = info.get("policy")
        p = policies.get(pol, {})
        phases = (p.get("policy") or {}).get("phases", {})
        if "warm" in phases or not pol:
            continue
        b, c = agg.get(pol, (0, 0))
        agg[pol] = (b + size_by_idx.get(iname, 0), c + 1)

    ranked = sorted(agg.items(), key=lambda kv: -kv[1][0])
    for pol, (b, c) in ranked:
        gb = b / 1024**3
        if gb < min_gb:
            continue
        f = Finding("ILM-{}".format(slugify(pol)), "P2", "FinOps", "política ILM",
                    "Política \"{}\" sem fase warm — {:.1f} GB em {} índice(s) presos no tier hot".format(pol, gb, c))
        f.evidence = [
            "`evidence/04-ilm-policies.json`: política `{}` não define fase `warm`.".format(pol),
            "`evidence/04-ilm-explain-managed.json` + `03-cat-indices.json`: {} índice(s) geridos por esta política somam {:.1f} GB, todos permanecendo no tier hot indefinidamente.".format(c, gb),
        ]
        f.root_cause = "Sem uma fase warm configurada, os índices desta política nunca migram para o tier mais barato por GB, independentemente da idade do dado."
        f.impact = "{:.1f} GB armazenados no tier mais caro (hot) sem necessidade, se o padrão de acesso a esses dados permitir tier mais barato.".format(gb)
        f.steps = [
            "Confirmar com o time dono do dado se consultas a esses índices ainda precisam da performance do tier hot após alguns dias/semanas.",
            "Se não precisarem: adicionar fase `warm` à política `{}` com um `min_age` apropriado (ex.: 7-30 dias, a definir com o time).".format(pol),
            "Sequenciar esta mudança DEPOIS de qualquer correção de capacidade do tier warm já identificada neste runbook (mandar mais dado para um tier já saturado piora o problema).",
        ]
        f.execution_location = "[KIBANA DEV TOOLS ou ELASTIC CLOUD CONSOLE] — IMPLEMENTAÇÃO FUTURA"
        f.validation_before = "GET _ilm/policy/{} — registrar phases atuais.".format(pol)
        f.validation_after = "Após min_age decorrido, confirmar via _cat/indices que os índices desta política migraram para os nós warm."
        f.rollback = "PUT _ilm/policy/{} restaurando as phases originais (sem warm).".format(pol)
        f.risk = "Baixo a médio (depende da capacidade disponível no tier warm no momento da migração)."
        f.effort = "Baixo por política; alto se aplicado a todas de uma vez sem faseamento."
        f.confidence = "Alta no dado (tamanho medido diretamente); média na recomendação de min_age (depende de padrão de acesso não medido nesta auditoria)."
        findings.append(f)
    return findings


def build_node_findings():
    findings = []
    nodes_stats = load_json("02-nodes-stats.json", {"nodes": {}})
    cat_nodes = load_json("02-cat-nodes.json", []) or []
    breaker_p0 = aa.CFG["BREAKER_TRIPPED_P0"]

    cat_by_name = {n.get("name"): n for n in cat_nodes}
    for node_id, node in (nodes_stats.get("nodes") or {}).items():
        name = node.get("name")
        tripped = ((node.get("breakers") or {}).get("parent") or {}).get("tripped", 0)
        if tripped < breaker_p0:
            continue
        heap_max = (node.get("jvm") or {}).get("mem", {}).get("heap_max_in_bytes")
        gc = ((node.get("jvm") or {}).get("gc", {}) or {}).get("collectors", {}).get("old", {})
        cat = cat_by_name.get(name, {})
        f = Finding("NODE-{}".format(slugify(name)), "P0", "Performance", "nó do cluster",
                    "Nó \"{}\" com circuit breaker disparando {} vezes".format(name, tripped))
        f.evidence = [
            "`evidence/02-nodes-breakers.json`/`02-nodes-stats.json`: node=`{}`, breakers.parent.tripped={}, heap_max_in_bytes={} ({:.0f} MB).".format(
                name, tripped, heap_max, (heap_max or 0) / 1024**2),
            "`evidence/02-cat-nodes.json`: cpu={}%, ram.percent={}%.".format(cat.get("cpu"), cat.get("ram.percent")),
            "`evidence/02-nodes-stats.json`: old-gen GC — {} coletas, {} ms cumulativos.".format(
                gc.get("collection_count"), gc.get("collection_time_in_millis")),
        ]
        f.root_cause = "Heap insuficiente para o padrão de consultas/agregações que chega até este nó — memória rejeita requisições (circuit breaker) sob pressão sustentada de GC."
        f.impact = "Consultas/agregações contra dados deste nó falham ou enfileiram; risco de afetar Transforms/alertas que consultem dados aqui."
        f.steps = [
            "Elastic Cloud Console -> Deployment -> Edit -> localizar o tier de dados deste nó -> aumentar o tamanho de instância (heap maior).",
            "Antes de aumentar hardware, investigar se há um padrão de consulta anormalmente caro concentrado neste nó (não tratar só como problema de capacidade).",
            "Após o redimensionamento, monitorar `breakers.parent.tripped` por 24-48h — deve parar de crescer ou crescer muito mais devagar.",
        ]
        f.execution_location = "[ELASTIC CLOUD CONSOLE] — IMPLEMENTAÇÃO FUTURA"
        f.validation_before = "GET _nodes/stats/breaker — registrar tripped atual ({}).".format(tripped)
        f.validation_after = "Repetir a mesma consulta 24-48h após o redimensionamento — crescimento de tripped deve ser próximo de zero."
        f.rollback = "Reverter o tamanho de instância para a configuração anterior no Elastic Cloud Console."
        f.risk = "Médio (rolagem de nó pode causar breve indisponibilidade parcial, mitigada por réplica em outro nó do mesmo tier, se existir)."
        f.effort = "Médio."
        f.confidence = "Alta."
        findings.append(f)
    return findings


def build_closed_index_findings(cat_indices):
    """Cruza mensagens de erro de transforms com o inventario de indices para achar
    indices fechados que nao aparecem em _cat/indices (sem expand_wildcards=all)."""
    findings = []
    known = {x["index"] for x in cat_indices}
    payload = load_json("07-transforms-stats.json", {"transforms": []})
    seen = set()
    for t in payload.get("transforms", []):
        reason = str(t.get("reason", ""))
        m = re.search(r"index \[([^\]]+)\] blocked by: \[FORBIDDEN/4/index closed\]", reason)
        if not m:
            continue
        idx_name = m.group(1)
        if idx_name in known or idx_name in seen:
            continue
        seen.add(idx_name)
        f = Finding("IDX-{}".format(slugify(idx_name)), "P2", "Blind spot / Governança", "índice",
                    "Índice \"{}\" está fechado e invisível no inventário padrão".format(idx_name))
        f.evidence = [
            "`evidence/07-transforms-stats.json`: transform `{}` bloqueado por `FORBIDDEN/4/index closed` no índice `{}`.".format(t.get("id"), idx_name),
            "`evidence/03-cat-indices.json` (coleta sem `?expand_wildcards=all`): `{}` NÃO aparece — confirma que índices fechados ficam fora da varredura padrão.".format(idx_name),
        ]
        f.root_cause = "audit_collect.py não usa `?expand_wildcards=all` em `_cat/indices` — índices fechados/ocultos não são inventariados. Este é o único caso com evidência indireta de existir; pode haver outros."
        f.impact = "Pode haver mais índices fechados por engano (ex.: evento de disco cheio não tratado) sem que ninguém veja, além de bloquear o transform acima."
        f.steps = [
            "Confirmar com o time de dados se o fechamento de `{}` foi intencional (arquivamento) ou acidental.".format(idx_name),
            "Se intencional: parar/reapontar o transform dependente em vez de deixá-lo falhando.",
            "Se acidental: reabrir com `POST {}/_open`.".format(idx_name),
            "Corrigir a coleta: adicionar `?expand_wildcards=all` (ou `open,closed`) à chamada de `_cat/indices` em `audit_collect.py`/`03-indices-and-shards.sh`, para que a próxima rodada veja todos os índices fechados, não só este.",
        ]
        f.execution_location = "[ELASTIC CLOUD CONSOLE / KIBANA DEV TOOLS] — IMPLEMENTAÇÃO FUTURA"
        f.validation_before = "GET {}/_settings — confirmar status atual.".format(idx_name)
        f.validation_after = "GET _cat/indices/{}?expand_wildcards=all — confirmar visibilidade.".format(idx_name)
        f.rollback = "POST {}/_close para reverter, se a reabertura for indevida.".format(idx_name)
        f.risk = "Baixo para investigar/reabrir; depende do motivo original do fechamento."
        f.effort = "Baixo."
        f.confidence = "Alta (evidência indireta direta, mas requer confirmação humana da intenção do fechamento)."
        findings.append(f)
    return findings


# ---------------------------------------------------------------------------
# Achados NARRATIVOS (poucos, exigem verificacao humana de conteudo) - lidos
# dos blocos ja escritos em remediation-plan.md
# ---------------------------------------------------------------------------

def parse_remediation_plan(path):
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=### \S+ — )", text)
    out = {}
    for block in blocks:
        m = re.match(r"### (\S+) — (.+)", block)
        if not m:
            continue
        item_id, title = m.group(1), m.group(2).strip()
        fields = []
        cur_label = None
        for line in block.splitlines()[1:]:
            fm = re.match(r"-\s*\*\*([^:*]+):\*\*\s?(.*)", line.strip())
            if fm:
                cur_label = fm.group(1).strip()
                fields.append([cur_label, fm.group(2).strip()])
            elif line.strip() and cur_label and not line.strip().startswith("---"):
                fields[-1][1] = (fields[-1][1] + " " + line.strip()).strip()
        out[item_id] = {"title": title, "fields": [(l, v) for l, v in fields if v]}
    return out


def parse_executive_summary(path):
    if not path.exists():
        return {"intro": "", "risks": []}
    text = path.read_text(encoding="utf-8")
    intro_m = re.search(r"\n\n(Coleta real.+?)\n\n##", text, re.S)
    intro = intro_m.group(1).strip() if intro_m else ""
    risks_m = re.search(r"## Top \d+ riscos.*?\n\n(.+?)\n\n##", text, re.S)
    risks = []
    if risks_m:
        for line in risks_m.group(1).splitlines():
            rm = re.match(r"\d+\.\s+\*\*\[(P\d)\]\s*(.+?)\*\*[,.]?\s*(.*)", line.strip())
            if rm:
                risks.append({"priority": rm.group(1), "headline": rm.group(2).rstrip("."), "detail": rm.group(3)})
    return {"intro": intro, "risks": risks}


# ---------------------------------------------------------------------------
# Renderizacao
# ---------------------------------------------------------------------------

def render_finding_card(f):
    def li(items):
        return "".join("<li>{}</li>".format(inline_md(i)) for i in items)

    chips = "".join('<span class="chip">{}</span>'.format(esc(c)) for c in
                     [f.category, f.object_type, f.effort and "Esforço: " + f.effort,
                      f.confidence and "Confiança: " + f.confidence.split(" ")[0].rstrip(".,;")] if c)

    blocks = []
    if f.evidence:
        blocks.append('<div class="subhead">Evidência</div><ul class="ev-list">{}</ul>'.format(li(f.evidence)))
    if f.root_cause:
        blocks.append('<div class="subhead">Causa raiz</div><p>{}</p>'.format(inline_md(f.root_cause)))
    if f.impact:
        blocks.append('<div class="subhead">Impacto</div><p>{}</p>'.format(inline_md(f.impact)))
    if f.steps:
        blocks.append('<div class="subhead">Passo a passo</div><ol class="steps">{}</ol>'.format(li(f.steps)))
    meta_rows = []
    for label, val in [("Local de execução", f.execution_location), ("Validação antes", f.validation_before),
                        ("Validação depois", f.validation_after), ("Rollback", f.rollback),
                        ("Risco", f.risk), ("Confiança", f.confidence)]:
        if val:
            meta_rows.append('<div class="field"><span class="field-label">{}</span><span class="field-value">{}</span></div>'.format(esc(label), inline_md(val)))
    if meta_rows:
        blocks.append('<div class="subhead">Execução e validação</div>{}'.format("".join(meta_rows)))

    return """
    <details class="finding-card" id="{anchor}" data-priority="{p}">
      <summary>
        <span class="pill pill-{p_lower}">{p}</span>
        <span class="finding-id">{fid}</span>
        <span class="finding-title">{title}</span>
        <span class="tag">{object_type}</span>
      </summary>
      <div class="finding-detail">
        <div class="chips">{chips}</div>
        {blocks}
      </div>
    </details>
    """.format(anchor=f.anchor(), p=f.priority, p_lower=f.priority.lower(), fid=esc(f.id),
               title=inline_md(f.title), object_type=esc(f.object_type), chips=chips, blocks="".join(blocks))


def render_narrative_card(item_id, block):
    fields = block["fields"]
    priority = "P2"
    for label, val in fields:
        if label.lower().startswith("prioridade"):
            priority = val.strip().split()[0].upper()
            if priority not in PRIORITY_ORDER:
                priority = "P2"
    rows = "".join(
        '<div class="field"><span class="field-label">{}</span><span class="field-value">{}</span></div>'.format(esc(l), inline_md(v))
        for l, v in fields
    )
    return """
    <details class="finding-card" id="{anchor}" data-priority="{p}">
      <summary>
        <span class="pill pill-{p_lower}">{p}</span>
        <span class="finding-id">{fid}</span>
        <span class="finding-title">{title}</span>
        <span class="tag">análise narrativa</span>
      </summary>
      <div class="finding-detail">{rows}</div>
    </details>
    """.format(anchor=slugify(item_id), p=priority, p_lower=priority.lower(), fid=esc(item_id),
               title=inline_md(block["title"]), rows=rows)


TEMPLATE = """<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{cluster_title} Runbook</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
:root {{
  --bg: #eef1f2; --surface: #ffffff; --surface-2: #e4e9eb; --surface-3: #d7dee1;
  --border: #c7d0d4; --text: #10161b; --text-muted: #4d5b63; --text-faint: #75838b;
  --accent: #0e6e76; --accent-contrast: #ffffff; --accent-soft: #dceeef;
  --sev-p0-bg: #fbe4e1; --sev-p0-fg: #8c231a; --sev-p0-border: #e8a79e;
  --sev-p1-bg: #fbeedd; --sev-p1-fg: #8a5211; --sev-p1-border: #e8c48c;
  --sev-p2-bg: #e3eef6; --sev-p2-fg: #1d5a82; --sev-p2-border: #a9cbe0;
  --sev-p3-bg: #ececed; --sev-p3-fg: #495057; --sev-p3-border: #c9ccd1;
  --code-bg: #10161b; --code-fg: #d9e6ea; --code-border: #29343a;
  --font-display: 'IBM Plex Sans', system-ui, sans-serif;
  --font-body: 'IBM Plex Sans', system-ui, sans-serif;
  --font-mono: 'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #0b1014; --surface: #121821; --surface-2: #1a222c; --surface-3: #232d38;
    --border: #2a3542; --text: #e7ecef; --text-muted: #9aa7b2; --text-faint: #71808c;
    --accent: #5cd9df; --accent-contrast: #05181a; --accent-soft: #113238;
    --sev-p0-bg: #3a1613; --sev-p0-fg: #ff9c8e; --sev-p0-border: #6b2a22;
    --sev-p1-bg: #3a2a10; --sev-p1-fg: #ffc57a; --sev-p1-border: #6b4b1c;
    --sev-p2-bg: #12293a; --sev-p2-fg: #82c1ec; --sev-p2-border: #204b69;
    --sev-p3-bg: #262a2e; --sev-p3-fg: #b7bec4; --sev-p3-border: #3a4046;
    --code-bg: #05080a; --code-fg: #cfe3e8; --code-border: #1c262c;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #0b1014; --surface: #121821; --surface-2: #1a222c; --surface-3: #232d38;
  --border: #2a3542; --text: #e7ecef; --text-muted: #9aa7b2; --text-faint: #71808c;
  --accent: #5cd9df; --accent-contrast: #05181a; --accent-soft: #113238;
  --sev-p0-bg: #3a1613; --sev-p0-fg: #ff9c8e; --sev-p0-border: #6b2a22;
  --sev-p1-bg: #3a2a10; --sev-p1-fg: #ffc57a; --sev-p1-border: #6b4b1c;
  --sev-p2-bg: #12293a; --sev-p2-fg: #82c1ec; --sev-p2-border: #204b69;
  --sev-p3-bg: #262a2e; --sev-p3-fg: #b7bec4; --sev-p3-border: #3a4046;
  --code-bg: #05080a; --code-fg: #cfe3e8; --code-border: #1c262c;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--text); font-family: var(--font-body); font-size: 0.9375rem; line-height: 1.6; -webkit-font-smoothing: antialiased; }}
.wrap {{ max-width: 76rem; margin: 0 auto; padding: 0 1.5rem 4rem; }}
a {{ color: var(--accent); }}
h1, h2, h3 {{ font-family: var(--font-display); text-wrap: balance; margin: 0; }}
.compliance-banner {{ background: var(--text); color: var(--bg); text-align: center; font-family: var(--font-mono); font-size: 0.75rem; letter-spacing: 0.02em; padding: 0.5rem 1rem; }}
header.hero {{ padding: 2.5rem 0 1.5rem; border-bottom: 1px solid var(--border); margin-bottom: 2rem; }}
.eyebrow {{ font-family: var(--font-mono); font-size: 0.6875rem; letter-spacing: 0.08em; text-transform: uppercase; color: var(--accent); font-weight: 600; display: block; margin-bottom: 0.5rem; }}
h1 {{ font-size: clamp(1.5rem, 3vw, 2.25rem); font-weight: 700; color: var(--text); }}
.hero-intro {{ max-width: 68ch; color: var(--text-muted); margin-top: 0.75rem; font-size: 1rem; }}
.stat-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr)); gap: 0.75rem; margin-top: 1.75rem; }}
.stat-tile {{ background: var(--surface); border: 1px solid var(--border); border-radius: 0.5rem; padding: 0.9rem 1rem; }}
.stat-tile .num {{ font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-size: 1.75rem; font-weight: 600; }}
.stat-tile .label {{ font-size: 0.75rem; color: var(--text-muted); margin-top: 0.15rem; }}
.stat-tile[data-p="P0"] .num {{ color: var(--sev-p0-fg); }}
.stat-tile[data-p="P1"] .num {{ color: var(--sev-p1-fg); }}
.stat-tile[data-p="P2"] .num {{ color: var(--sev-p2-fg); }}
.stat-tile[data-p="P3"] .num {{ color: var(--sev-p3-fg); }}
nav.filters {{ position: sticky; top: 0; background: var(--bg); z-index: 5; padding: 0.75rem 0; display: flex; gap: 0.9rem; flex-wrap: wrap; align-items: center; border-bottom: 1px solid var(--border); margin-bottom: 1.5rem; }}
nav.filters button {{ font-family: var(--font-mono); font-size: 0.8125rem; border: 1px solid var(--border); background: var(--surface); color: var(--text); padding: 0.4rem 0.85rem; border-radius: 999px; cursor: pointer; }}
nav.filters button.active {{ background: var(--accent); color: var(--accent-contrast); border-color: var(--accent); }}
nav.filters input {{ font-family: var(--font-mono); font-size: 0.8125rem; padding: 0.4rem 0.75rem; border: 1px solid var(--border); border-radius: 999px; background: var(--surface); color: var(--text); flex: 1; min-width: 12rem; }}
nav.filters button:focus-visible, a:focus-visible, details summary:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
.risk-list {{ list-style: none; margin: 1.5rem 0 0; padding: 0; display: grid; gap: 0.6rem; }}
.risk-list li {{ background: var(--surface); border: 1px solid var(--border); border-left: 3px solid var(--accent); border-radius: 0.35rem; padding: 0.75rem 1rem; font-size: 0.875rem; }}
.risk-list strong {{ font-family: var(--font-display); }}
section.priority-group {{ margin-bottom: 2.5rem; }}
section.priority-group h2 {{ font-size: 1.25rem; font-weight: 700; display: flex; align-items: center; gap: 0.6rem; margin-bottom: 1rem; }}
.card-list {{ display: grid; gap: 0.65rem; }}
details.finding-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 0.6rem; overflow: hidden; scroll-margin-top: 4rem; }}
details.finding-card[open] {{ border-color: var(--accent); }}
details.finding-card summary {{ list-style: none; cursor: pointer; padding: 0.85rem 1.1rem; display: flex; align-items: center; gap: 0.65rem; flex-wrap: wrap; }}
details.finding-card summary::-webkit-details-marker {{ display: none; }}
details.finding-card summary::before {{ content: "+"; font-family: var(--font-mono); color: var(--text-faint); width: 1rem; flex-shrink: 0; }}
details.finding-card[open] summary::before {{ content: "-"; }}
.finding-id {{ font-family: var(--font-mono); font-size: 0.75rem; color: var(--text-faint); }}
.finding-title {{ font-weight: 500; flex: 1; min-width: 12rem; }}
.pill {{ font-family: var(--font-mono); font-size: 0.6875rem; font-weight: 600; padding: 0.15rem 0.5rem; border-radius: 999px; border: 1px solid; letter-spacing: 0.02em; flex-shrink: 0; }}
.pill-p0 {{ background: var(--sev-p0-bg); color: var(--sev-p0-fg); border-color: var(--sev-p0-border); }}
.pill-p1 {{ background: var(--sev-p1-bg); color: var(--sev-p1-fg); border-color: var(--sev-p1-border); }}
.pill-p2 {{ background: var(--sev-p2-bg); color: var(--sev-p2-fg); border-color: var(--sev-p2-border); }}
.pill-p3 {{ background: var(--sev-p3-bg); color: var(--sev-p3-fg); border-color: var(--sev-p3-border); }}
.tag {{ font-size: 0.75rem; color: var(--text-muted); background: var(--surface-2); border-radius: 0.3rem; padding: 0.1rem 0.5rem; }}
.finding-detail {{ padding: 0 1.1rem 1.1rem; border-top: 1px solid var(--border); margin-top: 0.1rem; padding-top: 0.9rem; }}
.chips {{ display: flex; gap: 0.4rem; flex-wrap: wrap; margin-bottom: 0.9rem; }}
.chip {{ font-size: 0.75rem; background: var(--accent-soft); color: var(--accent); border-radius: 0.3rem; padding: 0.15rem 0.55rem; }}
.subhead {{ font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-faint); margin: 1rem 0 0.4rem; font-weight: 600; }}
.subhead:first-child {{ margin-top: 0; }}
.finding-detail p {{ margin: 0; }}
.ev-list, .steps {{ margin: 0; padding-left: 1.35rem; display: grid; gap: 0.35rem; }}
.steps {{ counter-reset: step; }}
.steps li {{ padding-left: 0.15rem; }}
.field {{ display: grid; grid-template-columns: 11rem 1fr; gap: 0.75rem; padding: 0.35rem 0; border-bottom: 1px dashed var(--surface-3); font-size: 0.875rem; }}
.field:last-child {{ border-bottom: none; }}
.field-label {{ color: var(--text-faint); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.03em; padding-top: 0.1rem; }}
.field-value code, p code, li code {{ font-family: var(--font-mono); background: var(--surface-2); padding: 0.05rem 0.3rem; border-radius: 0.25rem; font-size: 0.85em; }}
@media (max-width: 40rem) {{ .field {{ grid-template-columns: 1fr; }} }}
footer {{ margin-top: 3rem; padding-top: 1.5rem; border-top: 1px solid var(--border); color: var(--text-faint); font-size: 0.8125rem; }}
.empty-note {{ color: var(--text-muted); font-size: 0.875rem; padding: 0.75rem 0; }}
</style>

<div class="compliance-banner">NENHUMA ALTERACAO FOI EXECUTADA NO CLUSTER. TODAS AS MUDANCAS APRESENTADAS SAO APENAS RECOMENDACOES PARA IMPLEMENTACAO FUTURA.</div>

<div class="wrap">
  <header class="hero">
    <span class="eyebrow">Runbook técnico &middot; gerado em {generated_at} &middot; {total} achados individuais</span>
    <h1>{cluster_title} &mdash; guia de correção item a item</h1>
    <p class="hero-intro">{intro} Cada cartão abaixo é um objeto individual (uma regra, um watch, um transform, um índice, uma política, um nó) — nada agrupado. Evidência, causa raiz, passo a passo, comando, validação e rollback ficam todos dentro do próprio cartão.</p>
    <div class="stat-row">{stat_tiles}</div>
  </header>

  {risks_section}

  <nav class="filters" aria-label="Filtrar achados">
    <button type="button" class="active" data-filter="all">Todos</button>
    <button type="button" data-filter="P0">P0</button>
    <button type="button" data-filter="P1">P1</button>
    <button type="button" data-filter="P2">P2</button>
    <button type="button" data-filter="P3">P3</button>
    <input type="search" id="search-box" placeholder="Filtrar por nome, id, índice...">
  </nav>

  <main id="findings">
    {findings_html}
  </main>

  <footer>
    Gerado automaticamente por <code>scripts/read-only-audit/generate_runbook.py</code> a partir de
    <code>evidence/</code> (achados mecânicos, um cartão por objeto) e <code>remediation-plan.md</code>
    (achados que exigem verificação humana de conteúdo). Nenhum comando foi executado contra o cluster
    para gerar este relatório.
  </footer>
</div>

<script>
(function () {{
  var buttons = document.querySelectorAll("nav.filters button");
  var cards = document.querySelectorAll(".finding-card");
  var search = document.getElementById("search-box");
  var activeFilter = "all";

  function apply() {{
    var q = (search.value || "").toLowerCase();
    cards.forEach(function (c) {{
      var pOk = activeFilter === "all" || c.getAttribute("data-priority") === activeFilter;
      var qOk = !q || c.textContent.toLowerCase().indexOf(q) !== -1;
      c.style.display = (pOk && qOk) ? "" : "none";
    }});
  }}
  buttons.forEach(function (btn) {{
    btn.addEventListener("click", function () {{
      buttons.forEach(function (b) {{ b.classList.remove("active"); }});
      btn.classList.add("active");
      activeFilter = btn.getAttribute("data-filter");
      apply();
    }});
  }});
  search.addEventListener("input", apply);
}})();
</script>
"""


def main():
    rules = dedupe_by_id(load_pages("09-kibana-alerting-rules-page*.json"))
    if not rules:
        print("[generate_runbook] Nao encontrei regras em evidence/09-kibana-alerting-rules-page*.json - "
              "rode audit_collect.py antes de gerar o runbook.")
        return 1

    event_index = load_event_log_index()
    dataviews = load_dataviews_index()
    cat_indices = load_cat_indices()

    all_findings = []
    alert_findings, _groups = build_alert_findings(rules, event_index, cat_indices, dataviews)
    all_findings += alert_findings
    all_findings += build_monitoring_coverage_findings(rules, event_index)
    all_findings += build_transform_findings()
    all_findings += build_ml_job_findings()
    all_findings += build_watch_findings()
    all_findings += build_oversized_index_findings(cat_indices)
    all_findings += build_ilm_warm_gap_findings(cat_indices)
    all_findings += build_node_findings()
    all_findings += build_closed_index_findings(cat_indices)

    remediation = parse_remediation_plan(REMEDIATION_MD)
    mechanical_ids = {f.id for f in all_findings}
    # So' blocos NARR-* (achados que exigem verificacao humana de conteudo, nao
    # redutiveis a um padrao mecanico). Os antigos blocos BL-XXX (visao agrupada,
    # usada pelo prioritized-backlog.csv) ficam de fora do runbook - foram
    # substituidos pelos cartoes individuais mecanicos acima.
    narrative_blocks = {k: v for k, v in remediation.items() if k not in mechanical_ids and k.startswith("NARR-")}

    exec_summary = parse_executive_summary(EXEC_SUMMARY_MD)

    by_priority_count = {p: 0 for p in PRIORITY_ORDER}
    for f in all_findings:
        by_priority_count[f.priority] = by_priority_count.get(f.priority, 0) + 1
    for item_id, block in narrative_blocks.items():
        pr = "P2"
        for label, val in block["fields"]:
            if label.lower().startswith("prioridade"):
                pr = val.strip().split()[0].upper()
        if pr in by_priority_count:
            by_priority_count[pr] += 1

    total = len(all_findings) + len(narrative_blocks)
    stat_tiles = "".join(
        '<div class="stat-tile" data-p="{p}"><div class="num">{n}</div><div class="label">{p} individuais</div></div>'.format(
            p=p, n=by_priority_count.get(p, 0))
        for p in PRIORITY_ORDER
    )

    risks_html = ""
    if exec_summary["risks"]:
        items = "".join(
            '<li><span class="pill pill-{p_lower}">{p}</span> <strong>{headline}.</strong> {detail}</li>'.format(
                p_lower=r["priority"].lower(), p=r["priority"], headline=inline_md(r["headline"]), detail=inline_md(r["detail"]))
            for r in exec_summary["risks"]
        )
        risks_html = '<section><h2 style="font-size:1.25rem;">Panorama (sumário executivo)</h2><ul class="risk-list">{}</ul></section>'.format(items)

    grouped = {p: [] for p in PRIORITY_ORDER}
    for f in all_findings:
        grouped.setdefault(f.priority, []).append(f)
    narrative_by_priority = {p: [] for p in PRIORITY_ORDER}
    for item_id, block in narrative_blocks.items():
        pr = "P2"
        for label, val in block["fields"]:
            if label.lower().startswith("prioridade"):
                pr = val.strip().split()[0].upper()
        if pr not in PRIORITY_ORDER:
            pr = "P2"
        narrative_by_priority[pr].append((item_id, block))

    sections = []
    for p in PRIORITY_ORDER:
        cards = [render_finding_card(f) for f in grouped.get(p, [])]
        cards += [render_narrative_card(iid, blk) for iid, blk in narrative_by_priority.get(p, [])]
        if not cards:
            continue
        sections.append(
            '<section class="priority-group" data-priority-group="{p}"><h2><span class="pill pill-{pl}">{p}</span>{label} ({n})</h2>'
            '<div class="card-list">{cards}</div></section>'.format(
                p=p, pl=p.lower(), label=esc(PRIORITY_LABEL[p]), n=len(cards), cards="".join(cards))
        )
    findings_html = "".join(sections) if sections else '<p class="empty-note">Nenhum achado gerado.</p>'

    cluster_title = "Elastic Cluster"
    m = re.search(r'cluster de produ[cç][aã]o Elastic Cloud "([^"]+)"', exec_summary["intro"])
    if m:
        cluster_title = m.group(1).strip().title()

    out_html = TEMPLATE.format(
        cluster_title=esc(cluster_title),
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        total=total,
        intro=inline_md(exec_summary["intro"]) or "Runbook gerado diretamente da evidência coletada.",
        stat_tiles=stat_tiles,
        risks_section=risks_html,
        findings_html=findings_html,
    )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(out_html, encoding="utf-8")
    print("[generate_runbook] Escrito: {} ({} achados individuais: {} mecânicos + {} narrativos)".format(
        OUT_FILE, total, len(all_findings), len(narrative_blocks)))
    print("[generate_runbook] NENHUMA ALTERACAO FOI EXECUTADA NO CLUSTER.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
