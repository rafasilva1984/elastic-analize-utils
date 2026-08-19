#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_runbook.py
[LOCAL - 100% OFFLINE, SEM REDE, SEM GIT]

Le os documentos de achados ja escritos na raiz de elastic-observability-audit/
(prioritized-backlog.csv, remediation-plan.md, alert-recommendations.csv,
executive-summary.md) e monta um runbook visual autocontido em HTML: todos os
achados, agrupados por prioridade, com a evidencia, causa raiz, configuracao
atual/recomendada, comando exato (nunca executado por este script), validacao
e rollback de cada um.

Nao acessa a rede, nao acessa o Git, nao depende de nenhuma biblioteca externa
alem do Python 3.8+ padrao. So formata conteudo que ja foi escrito nos
documentos de analise - nao inventa nem recalcula nenhum achado.

Uso (apos rodar audit_analyze.py e a analise profunda que preenche os
documentos da raiz do pacote):

    python generate_runbook.py

Saida em elastic-observability-audit/report/runbook.html por padrao (mesma
pasta de saida do audit_analyze.py - nao versionada, ver .gitignore). Para
apontar para outra pasta (ex.: multiplos ambientes), use a mesma variavel de
audit_analyze.py:

    set AUDIT_REPORT_DIR=C:\\relatorios\\cluster-producao-br
    python generate_runbook.py

O arquivo gerado e um fragmento HTML autocontido (sem <!DOCTYPE>/<html>/<head>/
<body> - so <title>, <style> e o conteudo) para poder ser aberto diretamente
em um navegador OU publicado como estah, sem edicao, em qualquer ferramenta
que espere um fragmento (ex.: Artifacts).

IMPORTANTE - sensibilidade dos dados: o HTML gerado reproduz o conteudo real
dos documentos de achados (nomes de regras, indices, mensagens de erro) - o
mesmo dado sensivel que ja existe em evidence/ e nos .md/.csv da raiz do
pacote. NAO comite nem publique publicamente o runbook.html gerado sem a
mesma revisao manual ja exigida para evidence/ (ver README.md, secao "Status
deste repositorio").
"""

import csv
import html
import os
import re
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
AUDIT_ROOT = SCRIPT_DIR.parents[1]
DOCS_DIR = Path(os.environ.get("AUDIT_DOCS_DIR") or AUDIT_ROOT)
REPORT_DIR = Path(os.environ.get("AUDIT_REPORT_DIR") or (AUDIT_ROOT / "report"))

BACKLOG_CSV = DOCS_DIR / "prioritized-backlog.csv"
REMEDIATION_MD = DOCS_DIR / "remediation-plan.md"
ALERT_RECS_CSV = DOCS_DIR / "alert-recommendations.csv"
EXEC_SUMMARY_MD = DOCS_DIR / "executive-summary.md"
OUT_FILE = REPORT_DIR / "runbook.html"

PRIORITY_ORDER = ["P0", "P1", "P2", "P3"]
PRIORITY_LABEL = {
    "P0": "P0 - Critico / risco de indisponibilidade",
    "P1": "P1 - Ruido grave, saturacao ou falha recorrente",
    "P2": "P2 - Melhoria relevante de eficiencia/confiabilidade",
    "P3": "P3 - Otimizacao ou governanca",
}

FIELD_ORDER_HINT = [
    "Categoria", "Prioridade", "Evidencia", "Evidência",
    "Como foi identificada", "Situacao atual", "Situação atual",
    "Problema", "Impacto tecnico", "Impacto técnico",
    "Impacto operacional/negocio", "Impacto operacional/negócio",
    "Configuracao atual", "Configuração atual",
    "Configuracao recomendada", "Configuração recomendada",
    "Configuracao atual/recomendada", "Configuração atual/recomendada",
    "Justificativa", "Pre-requisitos", "Pré-requisitos", "Dependencias", "Dependências",
    "Risco", "Esforco", "Esforço", "Confianca", "Confiança",
    "Instrucoes exatas de implementacao", "Instruções exatas de implementação",
    "Local de execucao", "Local de execução",
    "Comando completo", "Comando exato", "Saida esperada", "Saída esperada",
    "Validacao anterior", "Validação anterior",
    "Validacao posterior", "Validação posterior",
    "Validacao anterior/posterior", "Validação anterior/posterior",
    "Criterio de sucesso", "Critério de sucesso",
    "Criterio de interrupcao", "Critério de interrupção",
    "Rollback completo", "Rollback", "Comando de rollback",
    "Referencia oficial", "Referência oficial",
]


def esc(text):
    return html.escape(str(text), quote=True)


def inline_md(text):
    """Minimal, safe inline markdown: escapes first, then re-enables **bold** and `code`."""
    text = esc(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`([^`]+?)`", r"<code>\1</code>", text)
    return text


def slugify(text):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower())
    return re.sub(r"-+", "-", s).strip("-")


def read_csv_rows(path):
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def parse_remediation_plan(path):
    """Returns {BL-ID: {"title": str, "fields": [(label, value), ...]}}"""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=### (?:BL|ALERT)-\S+ )", text)
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
    """Extracts the intro paragraph and the numbered top-risks list, if present."""
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
                headline = rm.group(2).rstrip(".")
                risks.append({"priority": rm.group(1), "headline": headline, "detail": rm.group(3)})
    return {"intro": intro, "risks": risks}


def render_field_list(fields):
    parts = []
    for label, value in fields:
        is_code = bool(re.search(r"(comando|command|referencia|referência)", label, re.I)) and (
            "http" in value or re.search(r"[A-Z]+ (api|_)", value)
        )
        if is_code or label.lower().startswith(("comando",)):
            parts.append(
                '<div class="field"><span class="field-label">{}</span>'
                '<pre class="code-block">{}</pre></div>'.format(esc(label), esc(value))
            )
        else:
            parts.append(
                '<div class="field"><span class="field-label">{}</span>'
                '<span class="field-value">{}</span></div>'.format(esc(label), inline_md(value))
            )
    return "\n".join(parts)


def priority_of(row, key="priority"):
    p = (row.get(key) or "").strip().upper()
    return p if p in PRIORITY_ORDER else "P3"


def build_backlog_section(backlog_rows, remediation):
    by_priority = {p: [] for p in PRIORITY_ORDER}
    for row in backlog_rows:
        by_priority[priority_of(row)].append(row)

    sections_html = []
    for p in PRIORITY_ORDER:
        rows = by_priority[p]
        if not rows:
            continue
        cards = []
        for row in rows:
            item_id = row.get("id", "")
            title = row.get("title", "")
            plan = remediation.get(item_id)
            anchor = slugify(item_id or title)
            meta_chips = "".join(
                '<span class="chip">{}</span>'.format(esc(v))
                for v in [row.get("category"), row.get("effort") and "Esforco: " + row.get("effort"),
                          row.get("confidence") and "Confianca: " + row.get("confidence")]
                if v
            )
            if plan and plan["fields"]:
                detail = render_field_list(plan["fields"])
            else:
                fallback_fields = [
                    ("Severidade", row.get("severity", "")),
                    ("Impacto", row.get("impact", "")),
                    ("Risco de mudanca", row.get("change_risk", "")),
                    ("Beneficio esperado", row.get("expected_benefit", "")),
                    ("Dependencias", row.get("dependencies", "")),
                    ("Evidencia", row.get("evidence_summary", "")),
                    ("Documento relacionado", row.get("related_document", "")),
                ]
                detail = render_field_list([(l, v) for l, v in fallback_fields if v])
            cards.append(
                """
                <details class="finding-card" id="{anchor}" data-priority="{p}">
                  <summary>
                    <span class="pill pill-{p_lower}">{p}</span>
                    <span class="finding-id">{item_id}</span>
                    <span class="finding-title">{title}</span>
                  </summary>
                  <div class="finding-detail">
                    {meta_chips}
                    {detail}
                  </div>
                </details>
                """.format(
                    anchor=anchor, p=p, p_lower=p.lower(), item_id=esc(item_id),
                    title=inline_md(title), meta_chips='<div class="chips">{}</div>'.format(meta_chips) if meta_chips else "",
                    detail=detail,
                )
            )
        sections_html.append(
            '<section class="priority-group" data-priority-group="{p}">'
            '<h2><span class="pill pill-{p_lower}">{p}</span>{label}</h2>'
            '<div class="card-list">{cards}</div>'
            "</section>".format(p=p, p_lower=p.lower(), label=esc(PRIORITY_LABEL[p]), cards="".join(cards))
        )
    return "\n".join(sections_html)


def build_alert_recs_section(rows, remediation):
    if not rows:
        return ""
    rows = sorted(rows, key=lambda r: (PRIORITY_ORDER.index(priority_of(r)) if priority_of(r) in PRIORITY_ORDER else 9, r.get("id", "")))
    items = []
    display_cols = [
        ("classification", "Classificacao"),
        ("evidence", "Evidencia"),
        ("justification", "Justificativa"),
        ("implementation_instruction", "Instrucao"),
        ("execution_location", "Local de execucao"),
        ("exact_command", "Comando"),
        ("pre_validation", "Validacao anterior"),
        ("post_validation", "Validacao posterior"),
        ("rollback_procedure", "Rollback"),
        ("confidence", "Confianca"),
    ]
    for row in rows:
        p = priority_of(row)
        plan = remediation.get(row.get("id", ""))
        if plan and plan["fields"]:
            detail = render_field_list(plan["fields"])
        else:
            fields = [(label, row.get(key, "")) for key, label in display_cols]
            detail = render_field_list([(l, v) for l, v in fields if v])
        items.append(
            """
            <details class="finding-card finding-card-compact" data-priority="{p}">
              <summary>
                <span class="pill pill-{p_lower}">{p}</span>
                <span class="finding-id">{item_id}</span>
                <span class="finding-title">{name}</span>
                <span class="tag">{classification}</span>
              </summary>
              <div class="finding-detail">{detail}</div>
            </details>
            """.format(
                p=p, p_lower=p.lower(), item_id=esc(row.get("id", "")),
                name=inline_md(row.get("alert_name", "")),
                classification=esc(row.get("classification", "")),
                detail=detail,
            )
        )
    return "".join(items)


def compute_stats(backlog_rows, alert_rows):
    counts = {p: 0 for p in PRIORITY_ORDER}
    for row in backlog_rows:
        counts[priority_of(row)] += 1
    alert_counts = {p: 0 for p in PRIORITY_ORDER}
    for row in alert_rows:
        alert_counts[priority_of(row)] += 1
    return counts, alert_counts


TEMPLATE = """<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{cluster_title} Runbook</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
:root {{
  --bg: #eef1f2;
  --surface: #ffffff;
  --surface-2: #e4e9eb;
  --surface-3: #d7dee1;
  --border: #c7d0d4;
  --text: #10161b;
  --text-muted: #4d5b63;
  --text-faint: #75838b;
  --accent: #0e6e76;
  --accent-contrast: #ffffff;
  --accent-soft: #dceeef;
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
body {{
  margin: 0; background: var(--bg); color: var(--text);
  font-family: var(--font-body); font-size: 0.9375rem; line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}}
.wrap {{ max-width: 74rem; margin: 0 auto; padding: 0 1.5rem 4rem; }}
a {{ color: var(--accent); }}
h1, h2, h3 {{ font-family: var(--font-display); text-wrap: balance; margin: 0; }}
.compliance-banner {{
  background: var(--text); color: var(--bg); text-align: center;
  font-family: var(--font-mono); font-size: 0.75rem; letter-spacing: 0.02em;
  padding: 0.5rem 1rem;
}}
header.hero {{ padding: 2.5rem 0 1.5rem; border-bottom: 1px solid var(--border); margin-bottom: 2rem; }}
.eyebrow {{
  font-family: var(--font-mono); font-size: 0.6875rem; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--accent); font-weight: 600; display: block; margin-bottom: 0.5rem;
}}
h1 {{ font-size: clamp(1.5rem, 3vw, 2.25rem); font-weight: 700; color: var(--text); }}
.hero-intro {{ max-width: 62ch; color: var(--text-muted); margin-top: 0.75rem; font-size: 1rem; }}
.stat-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr)); gap: 0.75rem; margin-top: 1.75rem; }}
.stat-tile {{
  background: var(--surface); border: 1px solid var(--border); border-radius: 0.5rem;
  padding: 0.9rem 1rem;
}}
.stat-tile .num {{ font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-size: 1.75rem; font-weight: 600; }}
.stat-tile .label {{ font-size: 0.75rem; color: var(--text-muted); margin-top: 0.15rem; }}
.stat-tile[data-p="P0"] .num {{ color: var(--sev-p0-fg); }}
.stat-tile[data-p="P1"] .num {{ color: var(--sev-p1-fg); }}
.stat-tile[data-p="P2"] .num {{ color: var(--sev-p2-fg); }}
.stat-tile[data-p="P3"] .num {{ color: var(--sev-p3-fg); }}
nav.filters {{ display: flex; gap: 0.5rem; flex-wrap: wrap; margin: 1.5rem 0 2rem; }}
nav.filters button {{
  font-family: var(--font-mono); font-size: 0.8125rem; border: 1px solid var(--border);
  background: var(--surface); color: var(--text); padding: 0.4rem 0.85rem; border-radius: 999px;
  cursor: pointer;
}}
nav.filters button.active {{ background: var(--accent); color: var(--accent-contrast); border-color: var(--accent); }}
nav.filters button:focus-visible, a:focus-visible, details summary:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
.risk-list {{ list-style: none; margin: 1.5rem 0 0; padding: 0; display: grid; gap: 0.6rem; }}
.risk-list li {{
  background: var(--surface); border: 1px solid var(--border); border-left: 3px solid var(--accent);
  border-radius: 0.35rem; padding: 0.75rem 1rem; font-size: 0.875rem;
}}
.risk-list strong {{ font-family: var(--font-display); }}
section.priority-group {{ margin-bottom: 2.5rem; }}
section.priority-group h2 {{
  font-size: 1.25rem; font-weight: 700; display: flex; align-items: center; gap: 0.6rem; margin-bottom: 1rem;
}}
.card-list {{ display: grid; gap: 0.75rem; }}
details.finding-card {{
  background: var(--surface); border: 1px solid var(--border); border-radius: 0.6rem; overflow: hidden;
}}
details.finding-card[open] {{ border-color: var(--accent); }}
details.finding-card summary {{
  list-style: none; cursor: pointer; padding: 0.85rem 1.1rem; display: flex; align-items: center;
  gap: 0.65rem; flex-wrap: wrap;
}}
details.finding-card summary::-webkit-details-marker {{ display: none; }}
details.finding-card summary::before {{
  content: "+"; font-family: var(--font-mono); color: var(--text-faint); width: 1rem; flex-shrink: 0;
}}
details.finding-card[open] summary::before {{ content: "-"; }}
.finding-id {{ font-family: var(--font-mono); font-size: 0.75rem; color: var(--text-faint); }}
.finding-title {{ font-weight: 500; flex: 1; min-width: 12rem; }}
.pill {{
  font-family: var(--font-mono); font-size: 0.6875rem; font-weight: 600; padding: 0.15rem 0.5rem;
  border-radius: 999px; border: 1px solid; letter-spacing: 0.02em; flex-shrink: 0;
}}
.pill-p0 {{ background: var(--sev-p0-bg); color: var(--sev-p0-fg); border-color: var(--sev-p0-border); }}
.pill-p1 {{ background: var(--sev-p1-bg); color: var(--sev-p1-fg); border-color: var(--sev-p1-border); }}
.pill-p2 {{ background: var(--sev-p2-bg); color: var(--sev-p2-fg); border-color: var(--sev-p2-border); }}
.pill-p3 {{ background: var(--sev-p3-bg); color: var(--sev-p3-fg); border-color: var(--sev-p3-border); }}
.tag {{
  font-size: 0.75rem; color: var(--text-muted); background: var(--surface-2); border-radius: 0.3rem;
  padding: 0.1rem 0.5rem;
}}
.finding-detail {{ padding: 0 1.1rem 1.1rem; border-top: 1px solid var(--border); margin-top: 0.1rem; padding-top: 0.9rem; }}
.chips {{ display: flex; gap: 0.4rem; flex-wrap: wrap; margin-bottom: 0.9rem; }}
.chip {{
  font-size: 0.75rem; background: var(--accent-soft); color: var(--accent); border-radius: 0.3rem;
  padding: 0.15rem 0.55rem;
}}
.field {{ display: grid; grid-template-columns: 12rem 1fr; gap: 0.75rem; padding: 0.4rem 0; border-bottom: 1px dashed var(--surface-3); font-size: 0.875rem; }}
.field:last-child {{ border-bottom: none; }}
.field-label {{ color: var(--text-faint); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.03em; padding-top: 0.15rem; }}
.field-value {{ color: var(--text); }}
.field-value code {{ font-family: var(--font-mono); background: var(--surface-2); padding: 0.05rem 0.3rem; border-radius: 0.25rem; font-size: 0.85em; }}
.code-block {{
  font-family: var(--font-mono); font-size: 0.8125rem; background: var(--code-bg); color: var(--code-fg);
  border: 1px solid var(--code-border); border-radius: 0.4rem; padding: 0.7rem 0.9rem; margin: 0;
  white-space: pre-wrap; word-break: break-word; overflow-x: auto;
}}
@media (max-width: 40rem) {{ .field {{ grid-template-columns: 1fr; }} }}
.appendix-search {{
  width: 100%; font-family: var(--font-mono); font-size: 0.875rem; padding: 0.6rem 0.9rem;
  border: 1px solid var(--border); border-radius: 0.5rem; background: var(--surface); color: var(--text);
  margin-bottom: 1rem;
}}
footer {{ margin-top: 3rem; padding-top: 1.5rem; border-top: 1px solid var(--border); color: var(--text-faint); font-size: 0.8125rem; }}
</style>

<div class="compliance-banner">NENHUMA ALTERACAO FOI EXECUTADA NO CLUSTER. TODAS AS MUDANCAS APRESENTADAS SAO APENAS RECOMENDACOES PARA IMPLEMENTACAO FUTURA.</div>

<div class="wrap">
  <header class="hero">
    <span class="eyebrow">Runbook de auditoria &middot; gerado em {generated_at}</span>
    <h1>{cluster_title} &mdash; achados e correcoes</h1>
    <p class="hero-intro">{intro}</p>
    <div class="stat-row">
      {stat_tiles}
    </div>
  </header>

  {risks_section}

  <nav class="filters" aria-label="Filtrar por prioridade">
    <button type="button" class="active" data-filter="all">Todos</button>
    <button type="button" data-filter="P0">P0</button>
    <button type="button" data-filter="P1">P1</button>
    <button type="button" data-filter="P2">P2</button>
    <button type="button" data-filter="P3">P3</button>
  </nav>

  <main id="backlog">
    {backlog_html}
  </main>

  <section id="alerting-appendix">
    <h2 style="margin: 2.5rem 0 1rem; font-size: 1.25rem;">Apendice &mdash; acoes recomendadas de alerting ({alert_count})</h2>
    <input type="search" class="appendix-search" id="alert-search" placeholder="Filtrar por nome, id ou classificacao...">
    <div class="card-list" id="alert-list">
      {alert_recs_html}
    </div>
  </section>

  <footer>
    Gerado automaticamente por <code>scripts/read-only-audit/generate_runbook.py</code> a partir dos
    documentos de analise em <code>elastic-observability-audit/</code>. Nenhum comando foi executado
    contra o cluster para gerar este relatorio.
  </footer>
</div>

<script>
(function () {{
  var buttons = document.querySelectorAll("nav.filters button");
  var cards = document.querySelectorAll(".finding-card");
  buttons.forEach(function (btn) {{
    btn.addEventListener("click", function () {{
      buttons.forEach(function (b) {{ b.classList.remove("active"); }});
      btn.classList.add("active");
      var f = btn.getAttribute("data-filter");
      cards.forEach(function (c) {{
        c.style.display = (f === "all" || c.getAttribute("data-priority") === f) ? "" : "none";
      }});
    }});
  }});
  var search = document.getElementById("alert-search");
  if (search) {{
    search.addEventListener("input", function () {{
      var q = search.value.toLowerCase();
      document.querySelectorAll("#alert-list .finding-card").forEach(function (c) {{
        c.style.display = c.textContent.toLowerCase().indexOf(q) !== -1 ? "" : "none";
      }});
    }});
  }}
}})();
</script>
"""


def main():
    if not BACKLOG_CSV.exists():
        print("[generate_runbook] Nao encontrei {} - rode a analise (audit_analyze.py + skill "
              "elastic-audit-deep-analysis) antes de gerar o runbook.".format(BACKLOG_CSV))
        return 1

    backlog_rows = read_csv_rows(BACKLOG_CSV)
    alert_rows = read_csv_rows(ALERT_RECS_CSV)
    remediation = parse_remediation_plan(REMEDIATION_MD)
    exec_summary = parse_executive_summary(EXEC_SUMMARY_MD)

    counts, alert_counts = compute_stats(backlog_rows, alert_rows)
    total = sum(counts.values())

    stat_tiles = "".join(
        '<div class="stat-tile" data-p="{p}"><div class="num">{n}</div><div class="label">{p} no backlog</div></div>'.format(
            p=p, n=counts[p]
        )
        for p in PRIORITY_ORDER
    )
    stat_tiles += '<div class="stat-tile"><div class="num">{n}</div><div class="label">Acoes de alerting</div></div>'.format(
        n=len(alert_rows)
    )

    risks_html = ""
    if exec_summary["risks"]:
        items = "".join(
            '<li><span class="pill pill-{p_lower}">{p}</span> <strong>{headline}.</strong> {detail}</li>'.format(
                p_lower=r["priority"].lower(), p=r["priority"],
                headline=inline_md(r["headline"]), detail=inline_md(r["detail"]),
            )
            for r in exec_summary["risks"]
        )
        risks_html = '<section><h2 style="font-size:1.25rem;">Riscos em destaque</h2><ul class="risk-list">{}</ul></section>'.format(items)

    backlog_html = build_backlog_section(backlog_rows, remediation)
    alert_recs_html = build_alert_recs_section(alert_rows, remediation)

    cluster_title = "Auditoria Elasticsearch/Kibana"
    m = re.search(r'cluster de produ[cç][aã]o Elastic Cloud "([^"]+)"', exec_summary["intro"])
    if m:
        cluster_title = 'Cluster "{}"'.format(m.group(1))

    out_html = TEMPLATE.format(
        cluster_title=esc(cluster_title),
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        intro=inline_md(exec_summary["intro"]) or "Runbook gerado a partir dos documentos de analise da auditoria.",
        stat_tiles=stat_tiles,
        risks_section=risks_html,
        backlog_html=backlog_html,
        alert_count=len(alert_rows),
        alert_recs_html=alert_recs_html,
    )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(out_html, encoding="utf-8")
    print("[generate_runbook] Escrito: {} ({} achados no backlog, {} acoes de alerting)".format(
        OUT_FILE, total, len(alert_rows)))
    print("[generate_runbook] NENHUMA ALTERACAO FOI EXECUTADA NO CLUSTER.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
