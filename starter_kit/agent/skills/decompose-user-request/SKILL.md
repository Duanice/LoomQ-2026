---
name: decompose-user-request
description: Decompose a LoomQ request into independently satisfiable tasks before generation, explanation, or backend selection begins.
---

# Decompose User Request

Read the complete user request and identify every independently satisfiable
outcome. This step plans work only; it does not answer questions, generate
QASM, or select a backend.

## Task Boundaries

- Create a separate task when the user asks a knowledge question and also asks
  for an operational result, even when both concern the same quantum topic.
- Create a separate task for every independently requested artifact or run.
  Two circuits with different desired outputs are two `circuit_build` tasks;
  task kinds may repeat.
- Keep constraints, examples, and desired output values with the task they
  modify; they are not separate tasks by themselves.
- Do not let a later construction request absorb an earlier question.
- Do not combine multiple requested artifacts merely because they share a task
  kind, verb, topic, or output format.
- Do not split one outcome merely because it contains several constraints or
  several sentences.
- Preserve concrete names, bit strings, quantities, and negations in each
  standalone `request`.
- Cover every requested outcome exactly once and do not invent extra work.

## Output Contract

Return exactly one JSON object with no surrounding text:

```json
{
  "label": "a concise Simplified Chinese summary of all tasks",
  "tasks": [
    {
      "id": "task_1",
      "kind": "quantum_concept",
      "request": "one standalone Simplified Chinese deliverable"
    }
  ]
}
```

`tasks` contains one to three items in the user's original order. IDs are
`task_1`, `task_2`, and `task_3`. `kind` is exactly one of:

- `quantum_concept`
- `product_help`
- `usage_help`
- `competition_help`
- `general_question`
- `circuit_build`
- `backend_select`

The label is at most 64 Unicode characters and each request is at most 180.

Before returning, check that a reader could complete each task independently,
that every interrogative, imperative, and separately requested artifact has
exactly one owner, and that merging any two tasks would hide a deliverable.
