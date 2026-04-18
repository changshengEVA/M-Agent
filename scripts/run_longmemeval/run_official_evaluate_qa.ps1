# Run LongMemEval official evaluate_qa via run_official_evaluate_qa.py (repo root = M-Agent).
# Usage (from repo root):
#   .\scripts\run_longmemeval\run_official_evaluate_qa.ps1
#   .\scripts\run_longmemeval\run_official_evaluate_qa.ps1 --dry-run --longmemeval-root D:\LongMemEval
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$repoRoot = (Resolve-Path (Join-Path $here "..\..")).Path
Set-Location $repoRoot
$py = Join-Path $here "run_official_evaluate_qa.py"
& python $py @args
exit $LASTEXITCODE
