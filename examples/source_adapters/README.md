# Source Adapter templates (v0.3 / v0.3.1)

External event sources should:

1. Keep any raw **Signal** private inside the adapter.
2. Normalize each event into a public [`Observation`](../../src/m_agent/sdk/stimulus/contracts.py), including an adapter-authored `stimulus_view` (the `[Current Stimulus]` body Thinking will show).
3. Call `runtime.ingest(observation)`.

Product Feedback / Schedule adapters follow the same pattern: Feedback renders provable tool results; Schedule renders due wake-up + deferred todo (not completion proof).

This package is **not** the full Cognitive Plugin SDK (that lands later). It only shows how to attach a Source Adapter to the Stimulus Kernel.

| Example | Purpose |
| --- | --- |
| `template_adapter.py` | Minimal Signal → Observation → ingest loop |
| `chat_adapter.py` | Chat user message Signal → Observation → ingest (v0.3.1) |
| `signed_webhook.py` | HMAC-signed webhook → Observation → ingest |
| `virtual_clock_source.py` | Deterministic Virtual Clock for offline replay |

Product Chat uses the runtime-owned adapter at
`m_agent.runtime.perception.chat_adapter`. This example mirrors that pattern for
external authors.

See also: `python -m m_agent.lab.stimulus --help` for offline Stimulus Lab.
