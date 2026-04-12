param(
  [string]$MagEnvName = "MAG",
  [switch]$UseCondaRun = $false
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..")
Set-Location $repoRoot

if ($UseCondaRun) {
  conda run -n $MagEnvName python scripts/run_locomo/sweep_locomo_hybrid_params.py `
    --test-id-prefix hybrid_grid `
    --dense-recall-topn 20 `
    --sparse-recall-topn 20 `
    --dense-weight 0.5,0.8 `
    --detail-topk 5,10,15 `
    --overwrite
}
else {
  python scripts/run_locomo/sweep_locomo_hybrid_params.py `
    --test-id-prefix hybrid_grid `
    --dense-recall-topn 20 `
    --sparse-recall-topn 20 `
    --dense-weight 0.5,0.8 `
    --detail-topk 5,10,15 `
    --overwrite
}
