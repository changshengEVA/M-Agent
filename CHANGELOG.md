# Changelog

All notable changes to M-Agent are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.1] - 2026-08-06

### Added

- Packaged default configuration, a `m-agent-chat` command, and wheel smoke
  validation for source-tree and installed usage.
- Safe, read-oriented tool defaults with an explicit opt-in profile for
  external writes, paired with a separate send-scoped Gmail configuration.
- Cross-platform CI for supported Python versions, dependency-boundary runs,
  semantic acceptance, migration checks, and distribution validation.

### Changed

- The default local RAG index uses stable, versioned embeddings and scoped
  recall metadata.
- Runtime thinking compiles the current user stimulus once and commits task
  state only after a successful turn.
- Schedule dispatch records leases and attempts so interrupted work can be
  reclaimed safely.
- Mutable runtime data and OAuth tokens resolve outside packaged, read-only
  configuration assets.
- LoCoMo, LongMemEval, and REALTALK are no longer vendored as dataset
  submodules; they remain optional external inputs for explicit loaders and
  evaluation workflows.

### Fixed

- Flush recovery and durable episode-note handling across restart boundaries.
- Runtime admission and Flush preparation now share a per-thread atomic fence;
  non-quiescent threads defer Flush instead of crossing a frozen boundary.
- Scene-tail selection now preserves the newest context under size limits.
- The packaged chat model explicitly uses the OpenAI-compatible provider and
  honors `OPENAI_MODEL`, avoiding LangChain's provider auto-detection drift.

### Security

- Removed credentials embedded in legacy DeepSeek and Neo4j examples; optional
  integrations now name environment-backed secrets instead of tracking values.

## [0.2.0] - 2026-07-13

### Added

- LangGraph-backed runtime with persistent Transactions, TaskState, Scene,
  Stimulus routing, Effect/Feedback, schedules, and tool-driven episodic memory.
- Deterministic runtime semantic acceptance and migration gates.

[Unreleased]: https://github.com/changshengEVA/M-Agent/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/changshengEVA/M-Agent/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/changshengEVA/M-Agent/releases/tag/v0.2.0
