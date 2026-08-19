# scripts/future-implementation

**ATENÇÃO: COMANDO NÃO EXECUTADO. DESTINADO SOMENTE À IMPLEMENTAÇÃO FUTURA POR PROFISSIONAL AUTORIZADO, APÓS APROVAÇÃO E JANELA DE MUDANÇA.**

Esta pasta conterá scripts de alteração (ex.: atualizar limiar de uma regra,
desabilitar um watch, ajustar política ILM, trocar alias após reindexação)
gerados a partir das recomendações em `../../remediation-plan.md` e
`../../alert-recommendations.csv`.

Nenhum script aqui foi executado durante a auditoria. Todos exigem:
1. Aprovação formal da mudança.
2. Janela de manutenção definida.
3. Execução manual por profissional autorizado, com as variáveis de
   ambiente configuradas por ele (nunca fixadas no script).
4. Validação prévia (`../future-validation/`) e plano de rollback
   (`../future-rollback/`) executados/prontos antes de qualquer mudança.

Status atual: **vazia**, pois a Fase 1 de coleta ainda não foi executada
contra um cluster real (ver `../../limitations.md`).
