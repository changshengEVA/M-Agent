# Contributing to M-Agent

Thanks for helping improve M-Agent. Small, focused changes with executable
tests are the easiest to review.

## Development setup

Python 3.10 or newer is required.

```bash
python -m venv .venv
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Activate the virtual environment using `.venv/Scripts/activate` on Windows or
`source .venv/bin/activate` on Linux and macOS.

## Before opening a pull request

Run the test suite:

```bash
python -m pytest
```

Changes to runtime semantics should also run the deterministic gates:

```bash
python -m m_agent.acceptance contract run --runtime langgraph_v1 --all-layers
python scripts/run_runtime_migration_gate.py --rounds 3
```

For packaging changes, build and validate both distributions:

```bash
python -m build
python -m twine check dist/*
```

Please update tests and `CHANGELOG.md` when behavior changes. Keep public APIs,
configuration migrations, recovery semantics, and security defaults explicit
in the pull request description.

## Configuration and secrets

Use synthetic fixtures in tests. Do not commit `.env`, `config/users/users.json`,
user-specific configuration, memory stores, checkpoints, or API credentials.
New external-write capabilities must remain opt-in and document their
side-effect classification.

## Commit and review scope

- Keep unrelated formatting and refactors out of functional changes.
- Preserve backward compatibility within the `0.2.x` line unless the change is
  a documented security or correctness fix.
- Add a regression test for bug fixes whenever practical.
- Link the issue or design document that explains non-trivial runtime changes.

By contributing, you agree that your contribution is licensed under the
project's MIT License.
