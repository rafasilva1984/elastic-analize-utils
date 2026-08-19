# scripts/read-only-audit

## Pipeline local em 2 passos (recomendado para uso recorrente / múltiplos ambientes)

Se você audita mais de um cluster/ambiente, o fluxo pensado para isso é:

```bat
:: 1) Coleta (acessa o cluster, so leitura)
set ELASTIC_URL=https://SEU-DEPLOY.es.SUA-REGIAO.aws.found.io
set KIBANA_URL=https://SEU-DEPLOY.kb.SUA-REGIAO.aws.found.io
python elastic-observability-audit\scripts\read-only-audit\audit_collect.py

:: 2) Analise (100% offline - le so a pasta evidence/, nao acessa rede nem Git)
python elastic-observability-audit\scripts\read-only-audit\audit_analyze.py
```

- **`audit_collect.py`** é o único passo que fala com o cluster. Grava tudo em `evidence/`.
- **`audit_analyze.py`** roda depois, **sem nenhum acesso a rede e sem nenhuma dependência do Git** —
  lê apenas o que está em `evidence/` e gera, em `report/`:
  - `alert-inventory.csv` (1 linha por regra de alerta, com histórico de 90 dias já preenchido se `event_log_alert_history()` tiver coletado)
  - `prioritized-backlog.csv` (achados P0-P3 gerados automaticamente: nós saturados, shards acima do limite de ILM, transforms/ML quebrados, alertas em erro, duplicatas por assinatura de conteúdo, data views/dashboards órfãos)
  - `findings-summary.md` (o mesmo conteúdo acima, em formato de leitura)
- **Nada é commitado, empurrado ou enviado a lugar nenhum automaticamente.** Os arquivos ficam só na
  sua máquina, na pasta `report/` (já no `.gitignore` do repositório). Você decide se quer olhar,
  compartilhar ou versionar.
- **Múltiplos ambientes:** aponte `AUDIT_EVIDENCE_DIR` e `AUDIT_REPORT_DIR` para pastas diferentes a
  cada cluster, para não misturar evidências/relatórios:
  ```bat
  set AUDIT_EVIDENCE_DIR=C:\auditorias\cluster-prod-br\evidence
  set AUDIT_REPORT_DIR=C:\auditorias\cluster-prod-br\report
  python audit_collect.py
  python audit_analyze.py
  ```
- **Limiares ajustáveis** (opcional) via variáveis de ambiente antes de rodar `audit_analyze.py` —
  ex.: `AUDIT_THRESHOLD_BREAKER_P0`, `AUDIT_THRESHOLD_CPU`, `AUDIT_THRESHOLD_ML_STALE_DAYS`. Ver o
  cabeçalho do script para a lista completa; os defaults funcionam bem na maioria dos casos.
- A lógica de análise é **genérica** — não há nenhum nome de cluster, índice ou regra específico
  fixado no código; o mesmo script funciona para qualquer `evidence/` gerado por `audit_collect.py`,
  de qualquer cluster. Validado nesta sessão rodando contra a evidência real já coletada (reproduziu
  de forma independente os mesmos achados P0/P1 identificados manualmente, e ainda achou casos novos,
  como uma regra chamada literalmente `"... [Clone]"`).
- **Honestidade da ferramenta:** classificações de duplicata/"sem uso" são heurísticas automáticas —
  seguem exigindo revisão humana antes de qualquer exclusão, exatamente como o restante desta
  auditoria.

## Passo 3 (opcional) — análise narrativa profunda, também local

`audit_analyze.py` é rápido e mecânico (thresholds). Se você quer a mesma profundidade de
investigação/redação usada nos documentos deste pacote (cruzamento de causa raiz entre categorias,
verificação de hipóteses antes de concluir, prosa completa) — sem depender desta sessão de chat —,
use o **Claude Code CLI localmente**, na sua máquina, com o skill já preparado no repositório:

```bat
:: Na raiz do repositorio clonado, depois de rodar os passos 1 e 2 acima
claude "use o skill elastic-audit-deep-analysis para atualizar os documentos da auditoria com a evidencia coletada"
```

Isso roda com a sua própria conta/assinatura Claude, local, e reescreve
`performance-analysis.md`, `duplicate-items.md`, `failed-and-unused-items.md`,
`alert-fatigue-analysis.md` + CSVs, `finops-analysis.md`, `resilience-analysis.md`,
`blind-spots.md`, `quick-wins.md`, `remediation-plan.md`, `executive-summary.md` e
`current-state-inventory.md` com achados reais do cluster que você acabou de auditar. Ver
`.claude/skills/elastic-audit-deep-analysis/SKILL.md` (raiz do repositório) para o passo a passo
exato que ele segue. Nada é commitado automaticamente — revise e decida você mesmo sobre o
`git add`/`commit`/`push`.

## Coleta (Fase 1) — duas formas equivalentes

Escolha uma — não é necessário rodar as duas. Ambas escrevem os mesmos nomes de
arquivo em `../../evidence/`, então os documentos de análise (que
referenciam `evidence/NN-xxx.json`) funcionam com qualquer uma delas.

## Opção A — Python puro (recomendado para Prompt do Windows / cmd.exe)

`audit_collect.py` usa **somente a biblioteca padrão** do Python (nenhum
`pip install` necessário). Requer Python 3.8+.

```bat
:: Prompt de Comando do Windows (cmd.exe)
set ELASTIC_URL=https://SEU-DEPLOY.es.SUA-REGIAO.aws.found.io
set KIBANA_URL=https://SEU-DEPLOY.kb.SUA-REGIAO.aws.found.io
python elastic-observability-audit\scripts\read-only-audit\audit_collect.py
```

Se `ELASTIC_API_KEY` (ou `ELASTIC_USERNAME`/`ELASTIC_PASSWORD`) não
estiver definida como variável de ambiente, o script pergunta no próprio
prompt com entrada mascarada (`getpass`) — a credencial nunca fica salva
em texto visível no `set`, no histórico do prompt nem em nenhum arquivo.

Funciona igualmente em PowerShell (`$env:ELASTIC_URL = "..."`) e em Git
Bash (`export ELASTIC_URL=...`).

Validado nesta rodada com um servidor HTTP local simulado (mock), cobrindo:
autenticação por API Key, paginação (Kibana Alerting Rules e Saved
Objects), listagem de repositórios de snapshot com nomes especiais,
tratamento de credencial inválida (401 → aborta com mensagem clara) e
cluster inacessível (connection refused → aborta com mensagem clara).
Não foi (nem podia ser) validado contra o cluster real desta sessão, pois
o ambiente onde isso rodou não tem rota de rede até `*.found.io` — por
isso o Python foi pedido para rodar na sua máquina.

## Opção B — Bash (Git Bash / WSL / Linux / macOS)

`00-check-env.sh` até `10-kibana-saved-objects.sh` + `run-all.sh`,
descritos no `README.md` raiz da auditoria. Requerem `curl` e `jq`
instalados.

## Em ambos os casos

- Nenhuma chamada de escrita é feita (ver cabeçalho de cada script/função).
- As evidências vão para `../../evidence/` (JSON bruto). Revise
  manualmente por dados sensíveis antes de qualquer commit — ver
  `../../evidence/README.md`.
- Ao final, nenhuma alteração terá sido feita no cluster.
