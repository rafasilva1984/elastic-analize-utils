# queries/read-only-audit

Corpos JSON usados pelos scripts em `scripts/read-only-audit/` para chamadas
`POST` que são semanticamente read-only (buscas com `size:0`, agregações,
ou endpoints de busca como `_watcher/_query/watches`). Nenhum arquivo aqui
é usado para criar, atualizar ou apagar dados.

- `watches-query.json` — usado por `08-watcher.sh` / `audit_collect.py::watcher()`.
- `event-log-alert-history-90d.json` — usado por `11-event-log-alert-history.sh` /
  `audit_collect.py::event_log_alert_history()`. Agregação (size:0) contra
  `.kibana-event-log-*` para obter disparos/recuperações/falhas por regra
  nos últimos 90 dias — fecha a lacuna registrada em `blind-spots.md` item 2.
- `agg-index-write-activity.json` — exemplo/modelo para checar atividade de
  escrita recente por índice/data stream (campo `@timestamp` deve ser
  ajustado ao mapping real antes do uso; ainda não foi executado contra o
  cluster real).
