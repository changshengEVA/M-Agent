# Source Adapter templates (v0.3 Public Alpha)

External event sources should:

1. Keep any raw **Signal** private inside the adapter.
2. Normalize each event into a public [`Observation`](../../src/m_agent/sdk/stimulus/contracts.py).
3. Call `runtime.ingest(observation)`.

This package is **not** the full Cognitive Plugin SDK (that lands later). It only shows how to attach a Source Adapter to the Stimulus Kernel.

| Example | Purpose |
| --- | --- |
| `template_adapter.py` | Minimal Signal → Observation → ingest loop |
| `signed_webhook.py` | HMAC-signed webhook → Observation → ingest |
| `virtual_clock_source.py` | Deterministic Virtual Clock for offline replay |

See also: `python -m m_agent.lab.stimulus --help` for offline Stimulus Lab.
