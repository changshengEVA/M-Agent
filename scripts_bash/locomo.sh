#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

MAG_ENV_NAME="${MAG_ENV_NAME:-MAG}"
USE_CONDA_RUN="${USE_CONDA_RUN:-1}"
PYTHON_BIN="${PYTHON_BIN:-python}"

if [[ "${USE_CONDA_RUN}" == "1" ]] && command -v conda >/dev/null 2>&1; then
  PY_CMD=(conda run -n "${MAG_ENV_NAME}" python)
else
  PY_CMD=("${PYTHON_BIN}")
fi

"${PY_CMD[@]}" scripts/sweep_locomo_hybrid_params.py \
  --test-id-prefix hybrid_grid \
  --dense-recall-topn 20 \
  --sparse-recall-topn 20 \
  --dense-weight 0.5,0.8 \
  --detail-topk 5,10,15 \
  --overwrite
