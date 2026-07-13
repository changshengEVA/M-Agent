# Think-life Perception and Transaction Refactor

## Invariants

- `Stimulus` contains only `kind`, readable `text`, and bounded `payload`.
- `PerceptionInput` carries thread, conversation, resolved transaction, dialogue, and scene context.
- `execution_feedback` returns to the same transaction and validates `active_delegate_id`.
- A conversation owns one chronological Scene log; transaction IDs are entry labels only.
- A transaction exclusively owns its `task_state` and `wm_entries`.
- Only one delegate may be active for a transaction in the current runtime.

## Thinking flow

1. Resolve a non-feedback stimulus to an existing or new transaction.
2. Generate the selected transaction's task-state update.
3. Make the execute / answer / silent decision.

Transaction resolution sees compact candidate summaries. Task-state generation sees the current
stimulus, dialogue, conversation scene, existing task state, and transaction WM. Decision-making
adds persona and capabilities.

## Implementation areas

- Perception and stimulus contracts
- Conversation identity propagation and conversation-keyed Scene storage
- Transaction registry, task state, and WM ownership
- Deterministic feedback attribution and semantic non-feedback attribution
- Thinking context and runtime prompt sections
- API snapshots, flush lifecycle, tests, and runtime specification
