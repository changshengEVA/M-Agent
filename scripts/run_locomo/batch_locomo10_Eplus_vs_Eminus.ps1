# Batch: LoCoMo10 full evaluation for E+ vs E- (per conv, with LLM judge).
# - Windows / PowerShell
# - Resumable: skips conv+variant runs that already have LLM-judge stats unless -Force
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_locomo\batch_locomo10_Eplus_vs_Eminus.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_locomo\batch_locomo10_Eplus_vs_Eminus.ps1 -Force
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_locomo\batch_locomo10_Eplus_vs_Eminus.ps1 -PythonExe "C:\path\to\python.exe"
#
param(
  [switch]$Force,
  [string]$PythonExe = "",
  [string]$TestPrefix = "locomo10_ab_local",
  [int]$JudgeConcurrency = 6
)

# Python logs INFO to stderr; do not treat stderr as terminating errors.
$ErrorActionPreference = "Continue"

function Test-LocomoLlmJudgeComplete {
  param([Parameter(Mandatory = $true)][string]$StatsPath)
  if (-not (Test-Path $StatsPath)) { return $false }
  try {
    $raw = Get-Content -LiteralPath $StatsPath -Raw -Encoding UTF8
    $j = $raw | ConvertFrom-Json
    $acc = $j.memory_agent_llm_judge.overall_accuracy
    return ($null -ne $acc)
  } catch { return $false }
}

function Ensure-Dir([string]$Path) {
  if (-not (Test-Path $Path)) { New-Item -ItemType Directory -Force -Path $Path | Out-Null }
}

function Get-ConvIdsFromData {
  param([Parameter(Mandatory = $true)][string]$Py)
  $cmd = @(
    "-c",
    "import json; from pathlib import Path; p=Path('data/locomo/data/locomo10.json'); " +
    "d=json.loads(p.read_text(encoding='utf-8')); ids=[]; " +
    "for x in d: ids.append(str(x.get('sample_id','')).strip()); " +
    "ids=[x for x in ids if x]; " +
    "seen=set(); out=[]; " +
    "for x in ids: " +
    "  if x not in seen: seen.add(x); out.append(x); " +
    "print(' '.join(out))"
  )
  $rawLines = & $Py @cmd 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to read conv ids from data/locomo/data/locomo10.json. python_exit=$LASTEXITCODE output=$rawLines"
  }
  $raw = ($rawLines | Out-String).Trim()
  return @($raw -split "\s+" | Where-Object { $_ -and $_.Trim() })
}

function Run-VariantForConv {
  param(
    [Parameter(Mandatory = $true)][string]$Py,
    [Parameter(Mandatory = $true)][string]$VariantName,
    [Parameter(Mandatory = $true)][string]$AgentConfig,
    [Parameter(Mandatory = $true)][string]$ConvId,
    [Parameter(Mandatory = $true)][string]$TestId
  )

  $runDir = Join-Path "log" $TestId
  Ensure-Dir $runDir
  $qaPath = Join-Path $runDir "locomo10_agent_qa.json"
  $statsPath = Join-Path $runDir "locomo10_agent_qa_stats.json"

  if ((-not $Force) -and (Test-LocomoLlmJudgeComplete -StatsPath $statsPath)) {
    Write-Host "SKIP  [$VariantName][$ConvId] (judge already present) -> $runDir"
    return
  }

  $evalArgs = @(
    "scripts\run_locomo\run_eval_locomo.py",
    "--config", $AgentConfig,
    "--conv-ids", $ConvId,
    "--workflow-id", ("locomo/" + $ConvId),
    "--test-id", $TestId,
    "--sample-fraction", "1.0",
    "--save-every", "1"
  )
  if ($Force -or -not (Test-Path $qaPath)) { $evalArgs += @("--overwrite") }

  Write-Host ("EVAL  [{0}][{1}] -> {2}" -f $VariantName, $ConvId, $runDir)
  & $Py @evalArgs *>&1 |
    Tee-Object -FilePath (Join-Path $runDir "run.console.log") |
    ForEach-Object {
      # Keep console quiet: only show tqdm progress + final summary lines.
      if ($_ -match "Evaluating QA:" -or $_ -match "Overall accuracy" -or $_ -match "Saved (predictions|stats)" ) {
        $_
      }
    } | Out-Host

  Write-Host ("JUDGE [{0}][{1}] -> {2}" -f $VariantName, $ConvId, $runDir)
  & $Py scripts\run_locomo\evaluate_agent_qa_llm_judge.py `
    --input $qaPath `
    --overwrite `
    --concurrency $JudgeConcurrency `
    *>&1 |
    Tee-Object -FilePath (Join-Path $runDir "judge.console.log") |
    ForEach-Object {
      # Show judge progress + final accuracy only.
      if ($_ -match "Evaluated \\d+/\\d+" -or $_ -match "Overall LLM judge accuracy") {
        $_
      }
    } | Out-Host
}

Push-Location "F:\AI\M-Agent"
try {
  $py = $PythonExe
  if (-not $py) { $py = "python" }

  # Stable timestamp for this batch.
  $ts = Get-Date -Format "yyyyMMdd_HHmmss"
  $progressLog = Join-Path "log" ("{0}__progress__{1}.log" -f $TestPrefix, $ts)
  Ensure-Dir "log"
  "=== start $(Get-Date -Format o) Force=$Force ===" | Out-File -FilePath $progressLog -Encoding utf8

  $convs = Get-ConvIdsFromData -Py $py
  if (-not $convs -or $convs.Count -eq 0) {
    # Fallback to the canonical LoCoMo10 conv list used across this repo.
    $convs = @("conv-26","conv-30","conv-41","conv-42","conv-43","conv-44","conv-47","conv-48","conv-49","conv-50")
  }

  $cfgEminus = "config\agents\memory\locomo_eval_memory_agent_full_mode_E2.yaml"
  $cfgEplus  = "config\agents\memory\locomo_eval_memory_agent_full_mode_Eplus.yaml"

  $total = $convs.Count
  $i = 0
  foreach ($c in $convs) {
    $i += 1
    $header = ("[{0}/{1}] conv={2}" -f $i, $total, $c)
    Write-Host ""
    Write-Host $header
    $header | Out-File -FilePath $progressLog -Append -Encoding utf8

    $testEminus = ("{0}__Eminus__{1}__{2}" -f $TestPrefix, $ts, $c)
    $testEplus  = ("{0}__Eplus__{1}__{2}"  -f $TestPrefix, $ts, $c)

    Run-VariantForConv -Py $py -VariantName "E-" -AgentConfig $cfgEminus -ConvId $c -TestId $testEminus
    Run-VariantForConv -Py $py -VariantName "E+" -AgentConfig $cfgEplus  -ConvId $c -TestId $testEplus

    ("DONE conv={0}" -f $c) | Out-File -FilePath $progressLog -Append -Encoding utf8
  }

  "=== end $(Get-Date -Format o) ===" | Out-File -FilePath $progressLog -Append -Encoding utf8
  Write-Host ""
  Write-Host ("Batch complete. Progress log: {0}" -f $progressLog)
} finally {
  Pop-Location
}

