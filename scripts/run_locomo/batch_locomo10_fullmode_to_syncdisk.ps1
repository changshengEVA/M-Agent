# Batch: LoCoMo10 full_mode (baseline prompts) per conv, copy logs to BaiduSyncdisk LOCOMO full_model tree.
# RESUMABLE: re-run this script after interruption; completed convs are skipped unless -Force.
#
# Requires: Neo4j up, MAG conda python, prebuilt memory under LOCOMO/full_model/GPT-4o-mini/locomo/conv-*
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\batch_locomo10_fullmode_to_syncdisk.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\batch_locomo10_fullmode_to_syncdisk.ps1 -Force

param(
  [switch]$Force
)

# Python logs INFO to stderr; do not treat stderr as terminating errors.
$ErrorActionPreference = "Continue"

function Test-LocomoLlmJudgeComplete {
  param(
    [Parameter(Mandatory = $true)]
    [string]$StatsPath
  )
  if (-not (Test-Path $StatsPath)) { return $false }
  try {
    $raw = Get-Content -LiteralPath $StatsPath -Raw -Encoding UTF8
    $j = $raw | ConvertFrom-Json
    $acc = $j.memory_agent_llm_judge.overall_accuracy
    return ($null -ne $acc)
  }
  catch {
    return $false
  }
}

function Test-LocomoConvArtifactDirComplete {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Dir
  )
  $qa = Join-Path $Dir "locomo10_agent_qa.json"
  $stats = Join-Path $Dir "locomo10_agent_qa_stats.json"
  if (-not (Test-Path $qa)) { return $false }
  return (Test-LocomoLlmJudgeComplete -StatsPath $stats)
}

# Avoid embedding non-ASCII paths in this file (encoding breaks on some hosts).
$memRoot = Get-ChildItem "f:\BaiduSyncdisk\M-Agent" -Directory -ErrorAction Stop |
  Where-Object { $_.Name -like "M-Agent-Memory*" } |
  Select-Object -First 1
if (-not $memRoot) { throw "Could not find M-Agent-Memory* under f:\\BaiduSyncdisk\\M-Agent" }
$srcRoot = Join-Path $memRoot.FullName "LOCOMO\full_model\GPT-4o-mini\locomo"
$dstMem = "F:\AI\M-Agent\data\memory\locomo"
$logBase = "F:\AI\M-Agent\log"
$destRoot = Join-Path $memRoot.FullName "LOCOMO\full_model\GPT-4o-mini-GPT-4.1-mini\log"
$py = "C:\Users\chang\.conda\envs\MAG\python.exe"
$magent = "F:\AI\M-Agent"
$config = "config\agents\memory\locomo_eval_memory_agent_full_mode__baseline_prompt.yaml"
$convs = @(
  "conv-26", "conv-30", "conv-41", "conv-42", "conv-43",
  "conv-44", "conv-47", "conv-48", "conv-49", "conv-50"
)

New-Item -ItemType Directory -Force -Path $destRoot | Out-Null
$progressLog = Join-Path $destRoot "locomo10_fullmode_local_batch_progress.log"
"=== batch start $(Get-Date -Format o) Force=$Force ===" | Out-File -FilePath $progressLog -Encoding utf8

foreach ($c in $convs) {
  "$(Get-Date -Format o) START $c" | Out-File -FilePath $progressLog -Append -Encoding utf8

  $testId = "locomo10_fullmode_local__$c"
  $runDir = Join-Path $logBase $testId
  $dest = Join-Path $destRoot $testId
  New-Item -ItemType Directory -Force -Path $runDir | Out-Null

  if ((-not $Force) -and (Test-LocomoConvArtifactDirComplete -Dir $dest)) {
    "$(Get-Date -Format o) SKIP $c (already complete in DEST)" | Out-File -FilePath $progressLog -Append -Encoding utf8
    continue
  }

  if ((-not $Force) -and (Test-LocomoConvArtifactDirComplete -Dir $runDir)) {
    "$(Get-Date -Format o) SYNC $c (RUNDIR complete -> DEST)" | Out-File -FilePath $progressLog -Append -Encoding utf8
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    robocopy $runDir $dest /E /MT:8 /NFL /NDL /NJH /NJS | Out-Null
    "$(Get-Date -Format o) DONE $c -> $dest" | Out-File -FilePath $progressLog -Append -Encoding utf8
    continue
  }

  $src = Join-Path $srcRoot $c
  if (-not (Test-Path $src)) {
    throw "Missing prebuilt memory: $src"
  }
  $dst = Join-Path $dstMem $c
  New-Item -ItemType Directory -Force -Path $dst | Out-Null
  robocopy $src $dst /E /MT:8 /NFL /NDL /NJH /NJS | Out-Null

  Push-Location $magent
  try {
    $qaPath = Join-Path $runDir "locomo10_agent_qa.json"
    $evalArgs = @(
      "scripts\run_locomo\run_eval_locomo.py",
      "--config", $config,
      "--conv-ids", $c,
      "--workflow-id", "locomo/$c",
      "--test-id", $testId,
      "--sample-fraction", "1.0"
    )
    if ($Force -or -not (Test-Path $qaPath)) {
      $evalArgs += @("--overwrite")
      "$(Get-Date -Format o) EVAL $c (mode=fresh_or_force)" | Out-File -FilePath $progressLog -Append -Encoding utf8
    }
    else {
      "$(Get-Date -Format o) EVAL $c (mode=resume_no_overwrite)" | Out-File -FilePath $progressLog -Append -Encoding utf8
    }

    & $py @evalArgs *>&1 | Out-File -FilePath (Join-Path $runDir "run.console.log") -Encoding utf8

    $statsPath = Join-Path $runDir "locomo10_agent_qa_stats.json"
    if (-not (Test-LocomoLlmJudgeComplete -StatsPath $statsPath)) {
      "$(Get-Date -Format o) JUDGE $c" | Out-File -FilePath $progressLog -Append -Encoding utf8
      & $py scripts\run_locomo\evaluate_agent_qa_llm_judge.py `
        --input $qaPath `
        --overwrite `
        --concurrency 4 `
        *>&1 | Out-File -FilePath (Join-Path $runDir "judge.console.log") -Encoding utf8
    }
    else {
      "$(Get-Date -Format o) JUDGE $c (skip, stats already contain LLM-judge)" | Out-File -FilePath $progressLog -Append -Encoding utf8
    }
  }
  finally {
    Pop-Location
  }

  New-Item -ItemType Directory -Force -Path $dest | Out-Null
  robocopy $runDir $dest /E /MT:8 /NFL /NDL /NJH /NJS | Out-Null

  "$(Get-Date -Format o) DONE $c -> $dest" | Out-File -FilePath $progressLog -Append -Encoding utf8
}

"=== batch end $(Get-Date -Format o) ===" | Out-File -FilePath $progressLog -Append -Encoding utf8
