# 05 Workflows + Steps child table
Do: `workflow`, `workflow_step` tables; `WorkflowService.replace_steps` (ordering, bump `steps_revision` only when steps change); Workflows screen with portal link field and add/remove/reorder steps; verify-step warning.
Accept: reorder persists; unchanged save does not bump revision; warning shown when no step contains "verify"; tests.
