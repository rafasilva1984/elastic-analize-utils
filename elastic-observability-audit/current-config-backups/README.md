# current-config-backups

Destinado a exportações **read-only e sanitizadas** das configurações
atuais (políticas ILM, templates, pipelines, regras de alerting,
conectores — sem segredos), capturadas **antes** de qualquer mudança
futura, para servir de base ao rollback.

**Este diretório fica fora do controle de versão por padrão** (ver
`.gitignore` na raiz do repositório, exceto este `README.md`) — mesmo
"sanitizado" de segredos, é configuração real de um cluster específico
(nomes de política, topologia de nós, licença) e não deve ser publicado.

Nomenclatura esperada ao popular localmente: `<recurso>-<data-da-coleta>.json`
(ex.: `ilm-policies-2026-08-19.json`, `cluster-settings-persistent-2026-08-19.json`,
`license-2026-08-19.json`, `slm-policies-2026-08-19.json`,
`node-topology-2026-08-19.json`). Antes de reutilizar qualquer arquivo daqui
fora deste repositório (ex.: anexar a um chamado), revise manualmente por
API keys, senhas, tokens ou segredos de conector — os scripts não devem
capturá-los, mas a revisão humana é obrigatória. Serve como baseline para
rollback das recomendações em `remediation-plan.md` que envolvem ILM,
cluster settings ou SLM.
