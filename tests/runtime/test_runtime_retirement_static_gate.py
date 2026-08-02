from __future__ import annotations

from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACTIVE_ROOTS = ("src", "config", "scripts", "tests")
# Per-user configuration is ignored operational state. It is migrated on
# authenticated load and belongs to the separately authorized data cutover,
# not to the tracked source/config CI surface.
USER_MANAGED_ROOTS = (PROJECT_ROOT / "config" / "users",)
TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}


def _retired_name_pattern() -> re.Pattern[str]:
    # Keep the forbidden product name out of the active gate source itself.
    return re.compile("".join(("think", "[_ -]?", "life")), re.IGNORECASE)


def test_active_backend_tree_has_no_retired_runtime_references() -> None:
    pattern = _retired_name_pattern()
    matches: list[str] = []
    for root_name in ACTIVE_ROOTS:
        root = PROJECT_ROOT / root_name
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if any(path.is_relative_to(item) for item in USER_MANAGED_ROOTS):
                continue
            relative = path.relative_to(PROJECT_ROOT).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            if pattern.search(relative) or pattern.search(text):
                matches.append(relative)

    assert matches == [], "retired Runtime references remain: " + ", ".join(
        sorted(matches)
    )
