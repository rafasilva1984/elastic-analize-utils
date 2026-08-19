# Auditoria Técnica Elasticsearch & Kibana — Produção

**MODO: AUDITORIA_READ_ONLY.**
**NENHUMA ALTERAÇÃO FOI EXECUTADA NO CLUSTER. TODAS AS MUDANÇAS APRESENTADAS SÃO APENAS RECOMENDAÇÕES PARA IMPLEMENTAÇÃO FUTURA.**

## Status deste repositório

Este repositório contém **apenas o ferramental reutilizável**: scripts de coleta read-only, o
analisador mecânico 100% offline (`audit_analyze.py`), o skill de análise narrativa profunda, e a
metodologia/critérios de classificação. Ele **não** contém resultado de nenhuma auditoria
específica.

`evidence/` (dados brutos coletados), `report/`, `current-config-backups/*.json` e os documentos de
achados na raiz de `elastic-observability-audit/` (`executive-summary.md`,
`performance-analysis.md`, `alert-inventory.csv`, etc.) são **gerados localmente** ao rodar o
pipeline contra um cluster real, e **nunca são commitados** (ver `.gitignore`) — cada ambiente
auditado pertence a um cliente/produção específico e é sensível por natureza, então fica
propositalmente fora do controle de versão. Rode o pipeline localmente (passo a passo abaixo) para
gerar esses documentos na sua própria máquina.

## Como retomar a auditoria (visão geral)

Duas formas equivalentes — escolha uma (detalhes em
[`scripts/read-only-audit/README.md`](scripts/read-only-audit/README.md)):

### Opção A — Prompt do Windows (cmd.exe), sem Git Bash, sem instalar nada

Requer apenas Python 3.8+ (já presente na maioria das instalações Windows
modernas; `audit_collect.py` usa só a biblioteca padrão, sem `pip install`).

1. **[PROMPT DO WINDOWS]** Configure `ELASTIC_URL` e `KIBANA_URL`:
   ```bat
   set ELASTIC_URL=https://SEU-DEPLOY.es.SUA-REGIAO.aws.found.io
   set KIBANA_URL=https://SEU-DEPLOY.kb.SUA-REGIAO.aws.found.io
   ```
2. **[PROMPT DO WINDOWS — READ-ONLY]** Execute a coleta completa:
   ```bat
   python elastic-observability-audit\scripts\read-only-audit\audit_collect.py
   ```
   Se `ELASTIC_API_KEY` não estiver definida como variável de ambiente, o
   script pergunta no próprio prompt com entrada mascarada — a credencial
   nunca aparece na tela nem fica salva no histórico do `set`.
3. **[PROMPT DO WINDOWS — 100% OFFLINE, SEM REDE, SEM GIT]** Gere o
   relatório de achados localmente, sem precisar de mim nem de push para
   o repositório (útil se você audita vários clusters/ambientes):
   ```bat
   python elastic-observability-audit\scripts\read-only-audit\audit_analyze.py
   ```
   Resultado em `elastic-observability-audit\report\` (`alert-inventory.csv`,
   `prioritized-backlog.csv`, `findings-summary.md`). Detalhes, variáveis
   de ambiente para múltiplos ambientes e limiares ajustáveis em
   [`scripts/read-only-audit/README.md`](scripts/read-only-audit/README.md).
4. **[CLAUDE CODE CLI LOCAL — opcional, para a análise narrativa completa]**
   Os passos 2-3 são mecânicos (thresholds). Para reescrever os documentos
   de análise com a mesma profundidade de investigação usada neste pacote
   (cruzamento de causa raiz, verificação de hipóteses, prosa completa —
   não apenas uma lista de limiares ultrapassados), rode localmente, na
   raiz do repositório clonado, com o Claude Code CLI e sua própria
   conta/assinatura:
   ```bat
   claude "use o skill elastic-audit-deep-analysis para atualizar os documentos da auditoria com a evidencia coletada"
   ```
   Skill em [`.claude/skills/elastic-audit-deep-analysis/SKILL.md`](../.claude/skills/elastic-audit-deep-analysis/SKILL.md).
   Roda inteiramente na sua máquina, sem depender desta conversa — funciona
   para quantos clusters/ambientes você precisar auditar. Nada é commitado
   automaticamente.

### Opção B — Git Bash

1. **[GIT BASH]** Configure as variáveis de ambiente (sem gravá-las em
   arquivos do repositório nem no histórico do shell):
   ```bash
   read -s -p "ELASTIC_URL: " ELASTIC_URL; echo; export ELASTIC_URL
   read -s -p "KIBANA_URL: " KIBANA_URL; echo; export KIBANA_URL
   read -s -p "ELASTIC_API_KEY: " ELASTIC_API_KEY; echo; export ELASTIC_API_KEY
   ```
2. **[GIT BASH — READ-ONLY]** Valide conectividade:
   ```bash
   bash elastic-observability-audit/scripts/read-only-audit/00-check-env.sh
   ```
3. **[GIT BASH — READ-ONLY]** Execute a coleta completa:
   ```bash
   bash elastic-observability-audit/scripts/read-only-audit/run-all.sh
   ```

### Depois da coleta (qualquer uma das opções)

As evidências brutas (JSON) serão salvas em `evidence/`.

4. Revise manualmente `evidence/` por dados sensíveis antes de qualquer
   commit ou compartilhamento (ver `evidence/README.md`).
5. Solicite a continuação da análise (Fases 2-5) informando que a coleta foi
   concluída — os documentos deste pacote serão então preenchidos com
   achados, evidências, classificações e recomendações específicas.

## Estrutura

```
elastic-observability-audit/
├── README.md                        (este arquivo)
├── executive-summary.md
├── current-state-inventory.md
├── blind-spots.md
├── alert-fatigue-analysis.md
├── alert-inventory.csv
├── alert-recommendations.csv
├── duplicate-items.md
├── failed-and-unused-items.md
├── performance-analysis.md
├── data-and-mapping-analysis.md
├── resilience-analysis.md
├── finops-analysis.md
├── quick-wins.md
├── prioritized-backlog.csv
├── remediation-plan.md
├── implementation-guide.md
├── validation-plan.md
├── rollback-plan.md
├── risks-and-dependencies.md
├── limitations.md
├── AUDIT-PROGRESS.md                (gerado pelo skill; checkpoint de retomada, nunca commitado)
├── queries/
│   ├── read-only-audit/        (corpos JSON para consultas GET/search)
│   ├── future-implementation/  (vazio — depende da Fase 1-5)
│   ├── future-validation/      (vazio — depende da Fase 1-5)
│   └── future-rollback/        (vazio — depende da Fase 1-5)
├── scripts/
│   ├── read-only-audit/        (scripts prontos para uso — ver acima)
│   ├── future-implementation/  (vazio — depende da Fase 1-5)
│   ├── future-validation/      (vazio — depende da Fase 1-5)
│   └── future-rollback/        (vazio — depende da Fase 1-5)
├── current-config-backups/     (vazio — depende da coleta real)
└── evidence/                   (vazio — destino da coleta real)
```

## Regras invioláveis desta auditoria

- Modo estritamente **read-only**. Nenhum script de `read-only-audit/`
  executa `PUT`/`POST` de escrita, `DELETE`, `_update_by_query`,
  `_delete_by_query`, `reindex`, `_execute` de watch, teste de conector,
  ou qualquer ação que altere estado do cluster, Kibana, alertas, watches,
  ILM, templates, pipelines, transforms, ML jobs, snapshots, segurança ou
  saved objects.
- Toda recomendação de mudança é **documentada, nunca executada**, com
  comando exato, validação prévia/posterior e rollback completo.
- Todo arquivo de implementação futura carrega o aviso:
  **"ATENÇÃO: COMANDO NÃO EXECUTADO. DESTINADO SOMENTE À IMPLEMENTAÇÃO
  FUTURA POR PROFISSIONAL AUTORIZADO, APÓS APROVAÇÃO E JANELA DE
  MUDANÇA."**
