import json
from pathlib import Path


def _load_single_sample(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or len(data) != 1 or "qa" not in data[0]:
        raise ValueError(f"Unexpected locomo10_agent_qa.json structure: {path}")
    return data[0]["qa"]


def _index_by_question(qa: list[dict]) -> dict[tuple[str, int], dict]:
    out: dict[tuple[str, int], dict] = {}
    for item in qa:
        key = (item.get("question"), int(item.get("category")))
        # last wins (should not collide)
        out[key] = item
    return out


def _is_pass(score: float | None, threshold: float = 0.5) -> bool:
    return score is not None and score >= threshold


def _used_entity_tools(item: dict) -> bool:
    calls = item.get("memory_agent_prediction_tool_calls") or []
    for c in calls:
        tn = (c.get("tool_name") or "").lower()
        if "entity" in tn and ("profile" in tn or "status" in tn):
            return True
    return False


def main() -> None:
    eminus_path = Path("log/conv48__Eminus__20260428_2006/locomo10_agent_qa.json")
    eplus_path = Path("log/conv48__Eplus__20260428_2055/locomo10_agent_qa.json")

    eminus = _index_by_question(_load_single_sample(eminus_path))
    eplus = _index_by_question(_load_single_sample(eplus_path))

    keys = set(eminus) | set(eplus)
    missing = 0
    both_good = 0
    both_bad = 0
    regressions: list[tuple[float, tuple[str, int], float, float]] = []
    improvements: list[tuple[float, tuple[str, int], float, float]] = []

    for k in keys:
        a = eminus.get(k)
        b = eplus.get(k)
        if not a or not b:
            missing += 1
            continue

        sa = a.get("memory_agent_llm_judge_score")
        sb = b.get("memory_agent_llm_judge_score")
        pa = _is_pass(sa)
        pb = _is_pass(sb)

        if pa and pb:
            both_good += 1
        elif (not pa) and (not pb):
            both_bad += 1
        elif pa and (not pb):
            regressions.append((sb - sa, k, sa, sb))
        else:
            improvements.append((sb - sa, k, sa, sb))

    regressions.sort()
    improvements.sort(reverse=True)

    reg_entity = sum(1 for _, k, _, _ in regressions if _used_entity_tools(eplus[k]))
    imp_entity = sum(1 for _, k, _, _ in improvements if _used_entity_tools(eplus[k]))

    print(f"total_keys={len(keys)} missing={missing}")
    print(
        f"both_good={both_good} both_bad={both_bad} "
        f"regressions={len(regressions)} improvements={len(improvements)}"
    )
    print(f"regressions_with_entity_calls={reg_entity}/{len(regressions)}")
    print(f"improvements_with_entity_calls={imp_entity}/{len(improvements)}")

    print("\nTop 30 regressions (Eminus pass, Eplus fail):")
    for d, k, sa, sb in regressions[:30]:
        q = (k[0] or "").replace("\n", " ")
        print(
            f"- cat={k[1]} entity={_used_entity_tools(eplus[k])} "
            f"judge(Eminus->Eplus)={sa:.3f}->{sb:.3f} | {q[:160]}"
        )


if __name__ == "__main__":
    main()

