# Stimulus Kernel (v0.3 / v0.3.1)

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
- `ignored` belongs to post-1.0 Attention (v1.x+) and is not a v0.3 disposition.

## Source Adapters

See [`examples/source_adapters/`](../../examples/source_adapters/README.md):

- template Signal → Observation → ingest
- chat user-message Signal → Observation → ingest (v0.3.1)
- signed webhook
- Virtual Clock replay source

Product Chat uses `m_agent.runtime.perception.chat_adapter.ChatSourceAdapter`.
`RuntimeHost.submit_user_message` is a compatibility facade over that adapter.

## Stimulus Lab

```powershell
python -m m_agent.lab.stimulus
python -m m_agent.lab.stimulus --scenario duplicate --json
python -m m_agent.lab.stimulus --scenario chat_duplicate --json
```

Required scenarios: duplicate, out-of-order, expired, irrelevant, valid.
Additional v0.3.1 scenario: `chat_duplicate` (chat idempotency via Adapter).

## Scope notes

- Public Alpha is for Source Adapter authors, not the full Cognitive Plugin SDK.
- **v0.3.1:** Chat sync/async product paths admit through `ChatSourceAdapter` → `runtime.ingest()`.
- Leaving the v0.3 line still requires clearing remaining non-Chat bypasses (schedule / feedback / gateway helpers) in a later 0.3.x gate.
