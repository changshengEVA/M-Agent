# Stimulus Kernel (v0.3 Public Alpha)

The Stimulus Kernel makes stimuli durable, auditable, first-class runtime objects.

## Public surface

| API / type | Module | Role |
| --- | --- | --- |
| `Observation` | `m_agent.sdk.stimulus` | Public ingress contract |
| `PublicStimulus` | `m_agent.sdk.stimulus` | Public admitted-stimulus view |
| `runtime.ingest(observation)` | `RuntimeHost` | Admit into the durable pool |
| `runtime.list_stimulus_trace(...)` | `RuntimeHost` | Append-only audit trail |

Adapter-private `Signal` is **not** a stable public SDK type. Keep it inside the Source Adapter.

## Vocabulary

**Pool state** (scheduling / recovery): `new | ready | running | waiting | terminated`

**Disposition** (terminal audit only): `rejected | merged | discarded | completed | aborted | failed`

- `deferred` is pool state `waiting`, not a disposition.
- `activated` means entering `running`.
- `ignored` belongs to v0.4 Attention and is not a v0.3 disposition.

## Source Adapters

See [`examples/source_adapters/`](../../examples/source_adapters/README.md):

- template Signal → Observation → ingest
- signed webhook
- Virtual Clock replay source

## Stimulus Lab

```powershell
python -m m_agent.lab.stimulus
python -m m_agent.lab.stimulus --scenario duplicate --json
```

Required scenarios: duplicate, out-of-order, expired, irrelevant, valid.

## Scope notes

- Public Alpha is for Source Adapter authors, not the full Cognitive Plugin SDK.
- Chat `submit_user_message` already thin-wraps `ingest`; migrating Chat through an explicit Source Adapter is a v0.3.x convergence task before leaving the v0.3 line.
