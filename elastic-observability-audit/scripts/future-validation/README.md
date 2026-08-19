# scripts/future-validation

Scripts read-only destinados a validar o estado **antes** e **depois** de
cada mudança futura descrita em `../future-implementation/`. Por serem
read-only, poderão ser executados livremente (inclusive antes da mudança,
para capturar a baseline), mas ainda não foram gerados porque dependem das
recomendações específicas, que dependem da coleta da Fase 1
(ver `../../limitations.md`).

Convenção planejada: cada script de implementação `NN-xxx.sh` terá um par
`NN-xxx-validate.sh` aqui, reutilizando `../read-only-audit/lib/common.sh`.
