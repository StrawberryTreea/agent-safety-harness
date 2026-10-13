param([string]$Out = "")

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"

$HarnessRoot = Split-Path -Parent $PSScriptRoot
$PublicTasks = Join-Path $PSScriptRoot "public_tasks.jsonl"
$PrivateRules = Join-Path $PSScriptRoot "private_rules.jsonl"
$RunRoot = if ($Out) {
    [System.IO.Path]::GetFullPath($Out)
} else {
    Join-Path $PSScriptRoot ("runs\smoke-" + (Get-Date -Format "yyyyMMddTHHmmss") + "-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
}
$CaptureRun = Join-Path $RunRoot "capture"
$ScoreRun = Join-Path $RunRoot "score"

Push-Location $HarnessRoot
try {
    if ((Test-Path -LiteralPath $RunRoot) -and
        ((-not (Test-Path -LiteralPath $RunRoot -PathType Container)) -or
         @(Get-ChildItem -LiteralPath $RunRoot -Force).Count -gt 0)) {
        throw "Use a new or empty output directory: $RunRoot"
    }

    python ash.py capture `
        --agent safe_stub `
        --input $PublicTasks `
        --out $CaptureRun
    if ($LASTEXITCODE -ne 0) { throw "capture failed with exit code $LASTEXITCODE" }

    python ash.py score `
        --private-input $PrivateRules `
        --capture-run $CaptureRun `
        --out $ScoreRun
    if ($LASTEXITCODE -ne 0) { throw "score failed with exit code $LASTEXITCODE" }

    python ash.py report --run $ScoreRun
    if ($LASTEXITCODE -ne 0) { throw "report failed with exit code $LASTEXITCODE" }
    Write-Host "Smoke evaluation passed. Results: $ScoreRun"
}
finally {
    Pop-Location
}
