# Security Policy

## Supported versions

M-Agent is currently an alpha project. Security fixes are applied to the
latest `0.2.x` release line; development snapshots and older release lines are
not supported separately.

| Version | Supported |
| --- | --- |
| Latest `0.2.x` | Yes |
| Older versions | No |

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability. Use the
repository's **Security → Report a vulnerability** workflow to submit a private
GitHub Security Advisory. Include the affected version, impact, reproduction
steps, and any suggested mitigation.

Maintainers will acknowledge a complete report as soon as practical, keep the
reporter informed while it is assessed, and coordinate disclosure after a fix
or mitigation is available.

Never include real API keys, tokens, private conversations, or production data
in a report. Replace them with minimal synthetic examples.

## Security defaults

The default tool profile is read-oriented. Capabilities that write to external
systems require an explicit opt-in configuration. Copy `.env.example` to a
local `.env`; never commit the populated file. Gmail is read-only by default.
Sending requires `chat_controller_external_writes.yaml`, which pairs the
external-write tool suite with a separate send-scoped Gmail configuration and
token.

## Credential hygiene

Tracked source and configuration must contain only environment-variable names
or empty placeholders, never credential values. If a credential is committed,
removing it in a later commit is not sufficient because it remains in Git
history: revoke or rotate it at the provider before publishing another release.
