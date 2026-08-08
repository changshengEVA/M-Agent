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

Product adapters under `m_agent.runtime.perception`:

| Adapter | Role |
| --- | --- |
| `ChatSourceAdapter` | user utterance; `stimulus_view` is a pointer (body stays in the user-role message) |
| `FeedbackSourceAdapter` | provable tool results only in `stimulus_view` |
| `ScheduleSourceAdapter` | due wake-up + deferred todo in `stimulus_view` (not completion proof) |

`Observation.stimulus_view` is the adapter-authored `[Current Stimulus]` body. Thinking assembles the titled block and does not branch on kind for Event/Objective/Evidence sections.

`RuntimeHost.submit_user_message` is a compatibility facade over `ChatSourceAdapter`.
Product schedule due and effect feedback admit through their adapters → `runtime.ingest()`.

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
- Product Chat / Feedback / Schedule now author `stimulus_view` at the Adapter edge.
- **Ingress Freeze:** acceptance harness and Gateway typed helpers (`submit_user_message` / `submit_execution_feedback` / `submit_heartbeat`) admit only through Observation → `admit_observation` / Adapter → `ingest`. Low-level `gateway.submit(envelope)` remains the internal pool admit used by that path.
