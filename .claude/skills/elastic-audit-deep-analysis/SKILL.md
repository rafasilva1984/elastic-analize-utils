---
name: elastic-audit-deep-analysis
description: Realiza a analise narrativa profunda (nao apenas mecanica) das evidencias ja coletadas em elastic-observability-audit/evidence/, atualizando todos os documentos de auditoria (performance-analysis.md, duplicate-items.md, failed-and-unused-items.md, resilience-analysis.md, finops-analysis.md, blind-spots.md, alert-fatigue-analysis.md + CSVs, quick-wins.md, prioritized-backlog.csv, remediation-plan.md, executive-summary.md, current-state-inventory.md). Use quando o usuario pedir para "analisar a auditoria elastic", "atualizar os documentos da auditoria com a evidencia nova", "rodar a analise profunda do cluster elastic/kibana", ou apos rodar audit_collect.py contra um novo cluster/ambiente.
---

# Análise Profunda de Auditoria Elasticsearch/Kibana

Você está continuando uma auditoria técnica read-only de um cluster Elasticsearch/Kibana de
produção. A coleta de dados (`scripts/read-only-audit/audit_collect.py`) já rodou e populou
`elastic-observability-audit/evidence/`. Seu trabalho aqui é a parte que exige raciocínio real:
ler a evidência, formar hipóteses, **verificá-las antes de escrever qualquer conclusão**, cruzar
achados entre categorias diferentes, e escrever os documentos de análise com a mesma densidade e
honestidade de um auditor sênior — não uma lista mecânica de thresholds ultrapassados.

Isto não é um checklist para preencher rápido. É um convite a investigar de verdade.

## Regra inegociável (herda do restante do pacote)

**Modo AUDITORIA_READ_ONLY.** Você pode ler qualquer arquivo em `evidence/`, rodar `jq`/`python3`
localmente para processar esses arquivos, e escrever/editar os documentos `.md`/`.csv` do pacote de
auditoria. Você **nunca** executa uma chamada de escrita contra o cluster (nada de `PUT`, `POST` de
criação/alteração, `DELETE`, nenhum comando via `curl`/Dev Tools que não seja um `GET` ou um `POST`
de busca/agregação). Se uma recomendação envolve mudança no cluster, ela é **documentada**, com o
aviso "ATENÇÃO: COMANDO NÃO EXECUTADO..." quando aplicável — nunca executada por você.

Este skill também **não interage com Git**: não faz `git add`/`commit`/`push`. Isso fica a critério
do usuário, depois que ele revisar o resultado.

## Checkpoint e retomada (leia isto antes do Passo 0)

Esta análise é grande (11+ documentos, dezenas de arquivos de evidência) e pode ultrapassar o
orçamento de uma única sessão/agente — já aconteceu (4 subagentes em paralelo bateram no limite
de sessão quase ao mesmo tempo e perderam o progresso). Por isso:

1. **Antes de começar**, verifique se existe `elastic-observability-audit/AUDIT-PROGRESS.md`. Se
   existir:
   - Confira o "fingerprint da evidência" registrado nele contra o estado atual de `evidence/`
     (contagem de arquivos `.json` + tamanho total). Se bater, é uma retomada de uma rodada
     interrompida — **não reinicie do zero**: pule direto para os documentos marcados
     `pendente`/`em andamento`, e reaproveite a seção "Achados-chave já confirmados" em vez de
     reinvestigar o que já foi verificado.
   - Se o fingerprint não bater, a evidência foi recoletada — trate como rodada nova (mas ainda
     assim reescreva/substitua `AUDIT-PROGRESS.md`, não acumule com o anterior).
   - Se não existir, crie-o antes de tocar em qualquer documento de achados (use a estrutura do
     próprio arquivo como referência, ou gere um novo com: fingerprint da evidência, tabela de
     status por documento — todos `pendente` — e seção de achados-chave vazia).
2. **Prefira trabalho sequencial a paralelismo agressivo** quando a tarefa é claramente grande:
   um subagente por vez (ou você mesmo, diretamente) consome a cota na mesma velocidade total,
   mas uma falha no meio custa só aquele documento, não vários de uma vez.
3. **Ao terminar (ou pausar) cada documento**, atualize a linha correspondente em
   `AUDIT-PROGRESS.md` (status + achados-chave que valham a pena reaproveitar depois) antes de
   seguir para o próximo. Trate isso como parte do trabalho, não como passo opcional no fim.
4. **Pare em um ponto seguro ao perceber sinais de estar perto do limite de uso/sessão**
   (erro explícito de "session limit"/"usage limit", ou qualquer sinal do ambiente nesse
   sentido): termine de escrever por completo o documento em andamento (nunca deixe um `.md`
   pela metade — prefira não tocar nele a deixá-lo inconsistente), grave o estado real em
   `AUDIT-PROGRESS.md`, e encerre o turno em vez de forçar mais uma chamada.
5. `AUDIT-PROGRESS.md` é estado efêmero (gitignored) — nunca é o produto final da análise, só o
   meio de retomá-la. Não referencie ele nos documentos de achados de verdade.

## Passo 0 — Orientação

1. Leia `elastic-observability-audit/README.md` e `elastic-observability-audit/limitations.md` para
   entender o estado atual: o que já foi coletado, quais lacunas existem, qual é a estrutura de
   documentos esperada.
2. Confirme que `elastic-observability-audit/evidence/` tem conteúdo real (não apenas o `README.md`
   da pasta). Se estiver vazia, pare e diga ao usuário para rodar `audit_collect.py` primeiro.
3. Verifique o tamanho dos arquivos antes de ler qualquer um por inteiro:
   ```bash
   cd elastic-observability-audit/evidence && du -h *.json | sort -h
   ```
   Arquivos grandes (na casa dos MB) **nunca** devem ser lidos brutos com a ferramenta de leitura de
   arquivo — processe-os com `jq`/`python3` extraindo só o que precisa, exatamente como faria um
   engenheiro de dados evitando estourar memória. Isso vale tanto para não desperdiçar seu próprio
   contexto quanto para não travar a máquina do usuário.

## Passo 1 — Rode a análise mecânica primeiro (ela é seu ponto de partida, não sua conclusão)

```bash
python3 elastic-observability-audit/scripts/read-only-audit/audit_analyze.py
```

Isso gera `elastic-observability-audit/report/prioritized-backlog.csv` e `report/findings-summary.md`
com achados objetivos baseados em threshold (circuit breaker, GC, CPU/RAM, shards acima do limite de
ILM, transforms/ML quebrados, alertas em erro, duplicatas por assinatura de conteúdo, saved objects
órfãos). **Trate essa lista como um checklist de pontos a investigar, não como o produto final.** A
diferença entre esse relatório automático e o trabalho que você vai fazer agora é exatamente a
investigação narrativa: por que aconteceu, o que mais isso explica, e se a hipótese automática
realmente se sustenta.

## Passo 2 — Investigação profunda, por área

Para cada achado do `prioritized-backlog.csv` automático (e para qualquer coisa que você notar
investigando por conta própria), aplique este ciclo:

1. **Leia a evidência bruta relevante** (`jq`/`python3` sobre os arquivos de `evidence/`).
2. **Cruze com pelo menos uma outra fonte** antes de concluir causa raiz. Exemplos do que isso
   significa na prática (achados reais de uma rodada anterior, para calibrar o nível esperado):
   - Um nó com heap pequeno teve o circuit breaker disparando dezenas de milhares de vezes
     (`evidence/02-nodes-breakers.json`). Isso por si só é um achado de performance. Mas cruzando com
     `evidence/07-transforms-stats.json` e achando um Transform falhando com a mensagem literal
     *"increase heap size on data nodes"*, o achado vira **confirmado por evidência de impacto real**,
     não apenas teórico — isso muda a prioridade e a redação.
   - Um alerta falha com `Unknown index [algo-000058]`. Antes de escrever "índice não existe", rode
     `jq -r '.[].index' evidence/03-cat-indices.json | grep algo` para confirmar qual é o índice atual
     — nesse caso descobriu-se que o índice tinha feito rollover para `-000060`, provando que a regra
     usa nome de índice hardcoded em vez de wildcard/alias. Essa é a diferença entre "está quebrado"
     (óbvio) e "está quebrado porque X, e vai quebrar de novo no próximo rollover a menos que Y"
     (útil).
   - Um índice geridos por ILM está em fase "hot" ocupando shard de 119 GB, e a política configura
     `max_primary_shard_size: 50gb`. Antes de concluir "ILM está quebrado", verifique
     `evidence/04-ilm-explain-managed.json` para o índice específico — se `action: complete`, o
     rollover *aconteceu*, só que tarde demais; isso é uma nuance importante para a recomendação
     (ajustar o limiar ou a frequência de poll, não "consertar o ILM que não está rodando").
3. **Verifique antes de classificar como duplicata.** Duas regras com nome idêntico não são
   necessariamente a mesma coisa — compare o conteúdo real (`params.esQuery`/`params.esqlQuery`/
   `params.index`) antes de declarar duplicata. Um caso real: duas regras chamadas exatamente igual
   ("... CANCEL SUCCESS DUPLICATED"), mesmo autor, criadas com 6 minutos de diferença — parecia óbvio
   que era um erro de duplo-clique. A consulta real revelou que uma filtrava `CREATE` e a outra
   `CANCEL` — não eram duplicatas, era um **nome errado em uma das duas**. Reporte o que você
   realmente encontrar, mesmo que contradiga a hipótese inicial mais óbvia — isso é o que dá
   credibilidade ao relatório.
4. **Nunca invente o que não dá para saber.** Se a informação não está na evidência coletada (ex.:
   histórico de 90 dias sem `evidence/11-event-log-alert-history-90d.json`, estimativa financeira sem
   tabela de preço), escreva explicitamente "não calculável com as informações disponíveis" ou "ND —
   requer [x]", nunca uma estimativa inventada. Isso vale especialmente para FinOps (nunca estime
   R$/US$ sem dado de custo real) e para alert fatigue (nunca classifique algo como "falso positivo"
   sem evidência de execução — use "provável ruído (inferência)" quando for uma suposição).

## Passo 3 — Estrutura e critérios de prioridade

Use os mesmos critérios já estabelecidos no pacote (ver `README.md`):

- **P0**: risco de indisponibilidade, perda de dados ou falha crítica — ou já causou falha confirmada
  (não apenas hipotética).
- **P1**: ruído grave, saturação, falhas recorrentes ou custo expressivo.
- **P2**: melhoria relevante de eficiência, confiabilidade ou manutenção.
- **P3**: otimização ou governança.

Cada recomendação relevante (pelo menos as P0/P1) deve seguir o formato completo já usado nos
documentos existentes: categoria, prioridade, evidência (arquivo + trecho), como foi identificada,
situação atual, problema, impacto técnico, impacto operacional/negócio, configuração atual vs.
recomendada, justificativa, pré-requisitos, dependências, risco, esforço, confiança, instruções
exatas (local de execução: `[KIBANA UI]`/`[KIBANA DEV TOOLS]`/`[ELASTIC CLOUD CONSOLE]` —
IMPLEMENTAÇÃO FUTURA), validação anterior/posterior, critério de sucesso/interrupção, rollback,
referência oficial. Veja `remediation-plan.md` do pacote para o padrão exato já usado.

Evite recomendações vagas ("ajustar shards", "revisar ILM", "otimizar queries"). Diga exatamente o
que foi encontrado, onde, com qual evidência, o que pode ser alterado e por quê.

## Passo 4 — Documentos a atualizar

Escreva/edite, com achados reais desta rodada (substituindo o conteúdo de uma rodada anterior, se
houver — esses documentos descrevem o cluster mais recentemente auditado, não um histórico
acumulado):

- `performance-analysis.md` — cluster/nós (heap, GC, breakers, CPU/RAM, thread pools), índices/shards
  (oversharding, shards grandes/pequenos, índices vazios), ILM/rollover, ingest pipelines, consultas.
- `duplicate-items.md` — grupos de objetos duplicados/sobrepostos, **sempre com verificação de
  conteúdo**, não só nome.
- `failed-and-unused-items.md` — falhas ativas (transforms, alertas, pipelines), candidatos a
  desativação com evidência + período de observação recomendado.
- `resilience-analysis.md` — snapshots/SLM, réplicas, distribuição por zona, SPOFs.
- `finops-analysis.md` — armazenamento por tier, política ILM vs. custo, recursos ociosos. Sem
  estimativa financeira em moeda sem dado de custo real.
- `blind-spots.md` — condições em que o ambiente pode falhar sem que ninguém seja alertado, com
  evidência de que a lacuna existe (não é uma lista de "boas práticas genéricas").
- `alert-fatigue-analysis.md` + `alert-inventory.csv` + `alert-recommendations.csv` — o núcleo do
  pedido original. Use `report/alert-inventory.csv` (gerado pelo `audit_analyze.py`) como base
  mecânica e enriqueça com a investigação narrativa dos casos mais relevantes (erros ativos,
  duplicatas confirmadas, regras sem ação).
- `quick-wins.md` — apenas itens de baixo esforço/risco com evidência clara. Nada é implementado.
- `prioritized-backlog.csv` — backlog final priorizado (pode partir do `report/prioritized-backlog.csv`
  automático, mas reescrito com os títulos/evidências refinados pela sua investigação).
- `remediation-plan.md` — detalhamento completo dos itens P0/P1 mais críticos, no formato do Passo 3.
- `executive-summary.md` e `current-state-inventory.md` — atualizados por último, resumindo o que foi
  encontrado nesta rodada especificamente (nomes de cluster, versões, contagens reais).
- `limitations.md` — atualize a seção de lacunas conhecidas: o que faltou coletar/analisar nesta
  rodada e por quê.

Todos esses arquivos já existem no repositório com uma estrutura/metodologia definida — leia o
arquivo existente antes de reescrever, para manter o padrão de seções e o tom já estabelecido, a
menos que a evidência desta rodada exija uma estrutura diferente.

## Passo 5 — Verificação final antes de terminar

```bash
# CSVs bem formados (mesma contagem de colunas em todas as linhas)
python3 -c "
import csv, glob
for fn in glob.glob('elastic-observability-audit/*.csv'):
    with open(fn, newline='', encoding='utf-8') as f:
        r = list(csv.reader(f))
        ncols = len(r[0])
        bad = [i for i,row in enumerate(r[1:],2) if len(row)!=ncols]
        print(fn, '-> linhas:', len(r), 'problema em:', bad)
"

# Nenhum documento de implementacao sem o aviso obrigatorio
grep -L "ATENÇÃO: COMANDO NÃO EXECUTADO" \
  elastic-observability-audit/remediation-plan.md \
  elastic-observability-audit/implementation-guide.md \
  elastic-observability-audit/rollback-plan.md 2>/dev/null
```

Confirme também, antes de considerar o trabalho pronto:

- Nenhum arquivo de `evidence/` foi alterado (você só lê essa pasta, nunca escreve nela — exceção:
  os arquivos gerados por `audit_collect.py`/`audit_analyze.py`, que não fazem parte do seu escopo
  aqui).
- Nenhuma credencial, token ou segredo foi copiado para dentro de nenhum documento `.md`/`.csv`.
- Cada afirmação factual nos documentos tem uma evidência rastreável (`evidence/NN-xxx.json` citado).
- O rodapé "NENHUMA ALTERAÇÃO FOI EXECUTADA NO CLUSTER. TODAS AS MUDANÇAS APRESENTADAS SÃO APENAS
  RECOMENDAÇÕES PARA IMPLEMENTAÇÃO FUTURA." continua presente onde já estava.

Ao terminar, resuma para o usuário (em texto, não em um novo arquivo): os principais achados P0/P1
desta rodada, quantos documentos foram atualizados, e que nada foi commitado — cabe a ele revisar e
decidir sobre o `git add`/`commit`/`push`.
