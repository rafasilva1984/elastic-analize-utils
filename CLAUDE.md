# elastic-analize-utils

Auditoria técnica read-only de um cluster Elasticsearch/Kibana de produção (Elastic Cloud).
Todo o trabalho real vive em `elastic-observability-audit/`.

## Regra inegociável — leia antes de qualquer coisa

**Modo AUDITORIA_READ_ONLY.** Nunca execute (nem sugira executar automaticamente) uma chamada de
escrita contra o cluster — nada de `PUT`/`POST` de criação ou alteração, `DELETE`,
`_update_by_query`, `_delete_by_query`, `reindex`, ativar/desativar regra ou watch, testar
conector, etc. Toda mudança recomendada é **documentada** (com o aviso "ATENÇÃO: COMANDO NÃO
EXECUTADO..."), nunca executada. Isso vale mesmo que a credencial disponível tenha permissão de
escrita. Detalhe completo em `elastic-observability-audit/README.md`.

## Estrutura

```
elastic-observability-audit/
├── README.md                 <- comece por aqui
├── executive-summary.md      <- resumo dos achados mais recentes
├── evidence/                 <- JSON bruto coletado do cluster (por ambiente)
├── report/                   <- saída de audit_analyze.py (gitignored, local)
├── scripts/read-only-audit/
│   ├── audit_collect.py      <- passo 1: fala com o cluster (so leitura)
│   ├── audit_analyze.py      <- passo 2: 100% offline, achados mecanicos
│   └── *.sh, lib/            <- equivalentes em bash
├── *.md, *.csv               <- documentos de analise (performance, alertas,
│                                 duplicatas, finops, resiliencia, etc.)
└── .claude/skills/elastic-audit-deep-analysis/SKILL.md  <- passo 3: analise narrativa
```

## Pipeline (3 passos, todos podem rodar localmente)

1. `python elastic-observability-audit/scripts/read-only-audit/audit_collect.py` — coleta contra
   o cluster real (precisa de `ELASTIC_URL`/`KIBANA_URL`/`ELASTIC_API_KEY`).
2. `python elastic-observability-audit/scripts/read-only-audit/audit_analyze.py` — achados
   mecânicos (thresholds), 100% offline, gera `report/*.csv` e `report/findings-summary.md`.
3. Skill `elastic-audit-deep-analysis` — análise narrativa profunda (cruzamento de causa raiz,
   verificação de hipóteses, prosa completa), atualiza os documentos `.md`/`.csv` na raiz de
   `elastic-observability-audit/`. Invoque com: "use o skill elastic-audit-deep-analysis para
   atualizar os documentos da auditoria com a evidência coletada".

## Múltiplos ambientes/clusters

`evidence/` e `report/` são específicos de **um** cluster por vez. Para auditar outro ambiente sem
misturar dados, use `AUDIT_EVIDENCE_DIR`/`AUDIT_REPORT_DIR` apontando para pastas separadas (ver
`elastic-observability-audit/scripts/read-only-audit/README.md`), ou trabalhe em um clone/branch
distinto por ambiente.

## Ao terminar qualquer análise

Nunca faça `git commit`/`push` automaticamente sem o usuário pedir — ele decide quando revisar e
enviar. Sempre confirme que nenhuma credencial ficou em nenhum arquivo antes de sugerir commit.
