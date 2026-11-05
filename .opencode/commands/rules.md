---
description: Review changes against the golden rules (read-only)
agent: reviewer
subagent: true
---

Review the current changes (working diff or the named files) against the `AGENTS.md` golden rules:
$ARGUMENTS

Order the findings: blocker → major → minor → nit, with `file:line` references. Finish with a one-paragraph summary and a recommended decision: merge / fix first / reject.
