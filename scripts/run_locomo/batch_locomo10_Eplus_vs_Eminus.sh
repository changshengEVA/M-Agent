#!/usr/bin/env bash
set -euo pipefail

# Batch: LoCoMo10 full evaluation for E+ vs E- (per conv, with LLM judge).
# - Linux / macOS (bash)
# - Resumable: skips conv+variant runs that already have LLM-judge stats unless FORCE=1
#
# Usage:
#   bash scripts/run_locomo/batch_locomo10_Eplus_vs_Eminus.sh
#   FORCE=1 bash scripts/run_locomo/batch_locomo10_Eplus_vs_Eminus.sh
#   PYTHON=python3 TEST_PREFIX=locomo10_ab_local JUDGE_CONCURRENCY=6 bash scripts/run_locomo/batch_locomo10_Eplus_vs_Eminus.sh

PYTHON="${PYTHON:-python3}"
TEST_PREFIX="${TEST_PREFIX:-locomo10_ab_local}"
JUDGE_CONCURRENCY="${JUDGE_CONCURRENCY:-6}"
FORCE="${FORCE:-0}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

ts="$(date +%Y%m%d_%H%M%S)"
mkdir -p log
progress_log="log/${TEST_PREFIX}__progress__${ts}.log"
echo "=== start $(date -Iseconds) FORCE=${FORCE} ===" | tee -a "$progress_log" >/dev/null

get_conv_ids() {
  "$PYTHON" -c "import json; from pathlib import Path; d=json.loads(Path('data/locomo/data/locomo10.json').read_text(encoding='utf-8')); ids=[]; \
for x in d: ids.append(str(x.get('sample_id','')).strip()); \
ids=[x for x in ids if x]; \
seen=set(); out=[]; \
for x in ids: \
  (out.append(x), seen.add(x)) if x not in seen else None; \
print(' '.join(out))"
}

test_llm_judge_complete() {
  local stats_path="$1"
  [[ -f "$stats_path" ]] || return 1
  "$PYTHON" -c "import json,sys; p=sys.argv[1]; j=json.loads(open(p,'r',encoding='utf-8').read()); \
acc=((j.get('memory_agent_llm_judge') or {}).get('overall_accuracy', None)); \
sys.exit(0 if acc is not None else 2)" "$stats_path"
}

run_variant_for_conv() {
  local variant_name="$1"
  local agent_config="$2"
  local conv_id="$3"
  local test_id="$4"

  local run_dir="log/${test_id}"
  mkdir -p "$run_dir"
  local qa_path="${run_dir}/locomo10_agent_qa.json"
  local stats_path="${run_dir}/locomo10_agent_qa_stats.json"

  if [[ "$FORCE" != "1" ]] && test_llm_judge_complete "$stats_path" >/dev/null 2>&1; then
    echo "SKIP  [${variant_name}][${conv_id}] (judge already present) -> ${run_dir}"
    return 0
  fi

  echo "EVAL  [${variant_name}][${conv_id}] -> ${run_dir}"
  eval_args=(scripts/run_locomo/run_eval_locomo.py
    --config "$agent_config"
    --conv-ids "$conv_id"
    --workflow-id "locomo/${conv_id}"
    --test-id "$test_id"
    --sample-fraction 1.0
    --save-every 1
  )
  if [[ "$FORCE" == "1" ]] || [[ ! -f "$qa_path" ]]; then
    eval_args+=(--overwrite)
  fi
  # Keep console quiet: only show tqdm progress + final summary lines.
  "$PYTHON" "${eval_args[@]}" 2>&1 \
    | tee "${run_dir}/run.console.log" \
    | grep -E 'Evaluating QA:|Overall accuracy|Saved (predictions|stats)' || true

  echo "JUDGE [${variant_name}][${conv_id}] -> ${run_dir}"
  "$PYTHON" scripts/run_locomo/evaluate_agent_qa_llm_judge.py \
    --input "$qa_path" \
    --overwrite \
    --concurrency "$JUDGE_CONCURRENCY" \
    2>&1 \
    | tee "${run_dir}/judge.console.log" \
    | grep -E 'Evaluated [0-9]+/[0-9]+|Overall LLM judge accuracy' || true
}

cfg_eminus="config/agents/memory/locomo_eval_memory_agent_full_mode_E2.yaml"
cfg_eplus="config/agents/memory/locomo_eval_memory_agent_full_mode_Eplus.yaml"

read -r -a convs <<<"$(get_conv_ids)"
if [[ "${#convs[@]}" -eq 0 ]]; then
  echo "No conv ids found in data/locomo/data/locomo10.json" >&2
  exit 2
fi

total="${#convs[@]}"
i=0
for c in "${convs[@]}"; do
  i=$((i+1))
  header="[${i}/${total}] conv=${c}"
  echo ""
  echo "$header"
  echo "$header" >>"$progress_log"

  test_eminus="${TEST_PREFIX}__Eminus__${ts}__${c}"
  test_eplus="${TEST_PREFIX}__Eplus__${ts}__${c}"

  run_variant_for_conv "E-" "$cfg_eminus" "$c" "$test_eminus"
  run_variant_for_conv "E+" "$cfg_eplus"  "$c" "$test_eplus"

  echo "DONE conv=${c}" >>"$progress_log"
done

echo "=== end $(date -Iseconds) ===" >>"$progress_log"
echo ""
echo "Batch complete. Progress log: ${progress_log}"

