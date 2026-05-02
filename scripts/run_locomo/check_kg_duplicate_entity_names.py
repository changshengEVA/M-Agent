#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""List Entity nodes in a workflow Neo4j DB that share the same normalized name."""

from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from typing import Dict, List

from m_agent.memory.memory_core.core.kg_base import KGBase

logger = logging.getLogger("run_locomo.check_kg_duplicate_entity_names")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Find duplicate Entity.name groups in a workflow KG.")
    p.add_argument("workflow_id", help="MemoryCore workflow_id, e.g. locomo-conv-48")
    p.add_argument("--max-print", type=int, default=40, help="Max duplicate groups to print.")
    args = p.parse_args()

    kg = KGBase(workflow_id=args.workflow_id.strip())
    ids = kg.list_entity_ids()
    by_name: Dict[str, List[str]] = defaultdict(list)
    for eid in ids:
        ok, ent = kg.get_entity(eid)
        if not ok or not ent:
            continue
        n = str(ent.get("name") or eid).strip().lower()
        if not n:
            n = str(eid).lower()
        by_name[n].append(str(eid))

    dup = [(n, xs) for n, xs in by_name.items() if len(xs) > 1]
    dup.sort(key=lambda x: (-len(x[1]), x[0]))

    print(f"workflow_id={args.workflow_id!r} database={kg.database!r}")
    print(f"entity_count={len(ids)} duplicate_name_groups={len(dup)}")
    for n, xs in dup[: max(0, args.max_print)]:
        print(f"  name={n!r} count={len(xs)} ids={xs}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
