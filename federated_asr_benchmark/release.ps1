[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Bootstrap', 'Preflight', 'Verify', 'Smoke', 'Adaptive', 'Fixed', 'Resume', 'Audit', 'Aggregate', 'Export')]
    [string]$Action,
    [string]$ManifestPath,
    [string]$Python,
    [string]$Request,
    [string]$CaseIndex,
    [string]$OutputRoot,
    [string]$CampaignDir,
    [string]$CampaignId,
    [string[]]$Products = @('codex', 'claude_code', 'qoder'),
    [string]$RunRoot,
    [string]$SelectionRoot,
    [string]$DataRoot,
    [string[]]$Results,
    [string]$RunId,
    [string]$Destination,
    [switch]$ConfirmLive,
    [switch]$StrictChecks
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
if (-not $Python) {
    $controllerPython = Join-Path $Root '.venvs/controller/Scripts/python.exe'
    $Python = if (Test-Path -LiteralPath $controllerPython) {
        $controllerPython
    } else {
        'python'
    }
}
if (-not $ManifestPath) {
    $exportedManifest = Join-Path $Root 'release-manifest.json'
    $developmentManifest = Join-Path $Root 'runs/release-dev/manifest.json'
    $ManifestPath = if (Test-Path -LiteralPath $exportedManifest) {
        $exportedManifest
    } else {
        $developmentManifest
    }
}

function Invoke-Python {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE"
    }
}

function Assert-Release {
    param([switch]$Required)
    if ($ManifestPath -and (Test-Path -LiteralPath $ManifestPath)) {
        Invoke-Python (Join-Path $Root 'tools/release_manifest.py') 'verify' '--root' $Root '--manifest' $ManifestPath
    } elseif ($Required) {
        throw "Release manifest not found: $ManifestPath. Supply -ManifestPath."
    }
}

function Assert-SourceLocks {
    param([Parameter(Mandatory = $true)][string]$SourceManifest)
    if ($DataRoot) {
        if (-not (Test-Path -LiteralPath $SourceManifest)) {
            throw "Source lock manifest not found: $SourceManifest"
        }
        Invoke-Python (Join-Path $Root 'tools/verify_sources.py') '--data-root' $DataRoot '--manifest' $SourceManifest
    }
}

function Assert-LiveConfirmation {
    if (-not $ConfirmLive) {
        throw 'Live product execution requires -ConfirmLive.'
    }
}

switch ($Action) {
    'Bootstrap' {
        Assert-Release
        if (-not $DataRoot) { throw 'Bootstrap requires -DataRoot.' }
        Assert-SourceLocks (Join-Path $Root 'selections/expansion/manifest.json')
        $pythonDetails = & $Python -c "import json,sys; print(json.dumps({'executable':sys.executable,'version':'.'.join(map(str,sys.version_info[:3]))}))"
        if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect the bootstrap Python interpreter.' }
        $pythonInfo = $pythonDetails | ConvertFrom-Json
        if ($pythonInfo.version -ne '3.13.2') {
            if ($StrictChecks) { throw "Bootstrap requires Python 3.13.2; observed $($pythonInfo.version)." }
            Write-Warning "Tested Python is 3.13.2; observed $($pythonInfo.version). Continuing with local runtime."
        }
        $pinnedPython = $pythonInfo.executable
        $venv = Join-Path $Root '.venvs/controller'
        if (-not (Test-Path -LiteralPath (Join-Path $venv 'Scripts/python.exe'))) {
            & $pinnedPython -m venv $venv
            if ($LASTEXITCODE -ne 0) { throw 'Failed to create controller environment.' }
        }
        & (Join-Path $venv 'Scripts/python.exe') -m pip install --disable-pip-version-check -r (Join-Path $Root 'requirements-controller.lock')
        if ($LASTEXITCODE -ne 0) { throw 'Failed to install controller dependencies.' }
        $qoderRuntime = Join-Path $Root '.runtime/qodercli-1.0.48'
        $qoderExecutable = Join-Path $qoderRuntime 'node_modules/.bin/qodercli.cmd'
        if (-not (Test-Path -LiteralPath $qoderExecutable)) {
            New-Item -ItemType Directory -Force -Path $qoderRuntime | Out-Null
            Copy-Item -LiteralPath (Join-Path $Root 'release/qodercli-runtime/package.json') -Destination $qoderRuntime
            Copy-Item -LiteralPath (Join-Path $Root 'release/qodercli-runtime/package-lock.json') -Destination $qoderRuntime
            & npm ci --prefix $qoderRuntime --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw 'Failed to install pinned Qoder CLI runtime.' }
        }
        $qoderVersion = & $qoderExecutable --version
        if ($LASTEXITCODE -ne 0 -or $qoderVersion -notmatch '1\.0\.48') {
            if ($StrictChecks) { throw "Qoder CLI runtime pin failed; observed $qoderVersion." }
            Write-Warning "Tested Qoder CLI version is 1.0.48; observed $qoderVersion. Continuing with local runtime."
        }
        $codebuddyRuntime = Join-Path $Root '.runtime/codebuddycli-2.133.1'
        $codebuddyExecutable = Join-Path $codebuddyRuntime 'node_modules/@tencent-ai/codebuddy-code/bin/codebuddy'
        if (-not (Test-Path -LiteralPath $codebuddyExecutable)) {
            New-Item -ItemType Directory -Force -Path $codebuddyRuntime | Out-Null
            Copy-Item -LiteralPath (Join-Path $Root 'release/codebuddycli-runtime/package.json') -Destination $codebuddyRuntime
            Copy-Item -LiteralPath (Join-Path $Root 'release/codebuddycli-runtime/package-lock.json') -Destination $codebuddyRuntime
            & npm ci --prefix $codebuddyRuntime --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw 'Failed to install pinned CodeBuddy CLI runtime.' }
        }
        $codebuddyVersion = & node $codebuddyExecutable --version
        if ($LASTEXITCODE -ne 0 -or $codebuddyVersion -notmatch '2\.133\.1') {
            if ($StrictChecks) { throw "CodeBuddy CLI runtime pin failed; observed $codebuddyVersion." }
            Write-Warning "Tested CodeBuddy CLI version is 2.133.1; observed $codebuddyVersion. Continuing with local runtime."
        }
        $uv = Join-Path $venv 'Scripts/uv.exe'
        foreach ($runtime in @(
            @{ Name = 'agentdojo'; Project = (Join-Path $DataRoot 'AgentDojo') },
            @{ Name = 'agentdyn'; Project = (Join-Path $DataRoot 'AgentDyn-main/AgentDyn-main') }
        )) {
            $runtimeEnvironment = Join-Path $Root ".venvs/$($runtime.Name)"
            $previousProjectEnvironment = $env:UV_PROJECT_ENVIRONMENT
            try {
                $env:UV_PROJECT_ENVIRONMENT = $runtimeEnvironment
                & $uv sync --frozen --no-dev --python $pinnedPython --project $runtime.Project
                if ($LASTEXITCODE -ne 0) { throw "Failed to synchronize $($runtime.Name) runtime." }
            } finally {
                $env:UV_PROJECT_ENVIRONMENT = $previousProjectEnvironment
            }
        }
        $agentPoisonEnvironment = Join-Path $Root '.venvs/agentpoison'
        if (-not (Test-Path -LiteralPath (Join-Path $agentPoisonEnvironment 'Scripts/python.exe'))) {
            & $pinnedPython -m venv $agentPoisonEnvironment
            if ($LASTEXITCODE -ne 0) { throw 'Failed to create AgentPoison environment.' }
        }
        & $uv pip sync --python (Join-Path $agentPoisonEnvironment 'Scripts/python.exe') --torch-backend cu128 (Join-Path $Root 'requirements-agentpoison.lock')
        if ($LASTEXITCODE -ne 0) { throw 'Failed to synchronize AgentPoison runtime.' }
    }
    'Verify' { Assert-Release -Required }
    'Preflight' {
        Assert-Release
        if (-not $DataRoot) { throw 'Preflight requires -DataRoot.' }
        Assert-SourceLocks (Join-Path $Root 'selections/expansion/manifest.json')
        Invoke-Python (Join-Path $Root 'tools/preflight.py') '--data-root' $DataRoot
    }
    'Smoke' {
        Assert-Release
        Assert-LiveConfirmation
        if (-not $Request) { throw 'Smoke requires -Request.' }
        $arguments = @((Join-Path $Root 'tools/run_product_batch.py'), '--request', $Request, '--limit', '5', '--keep-going')
        if ($DataRoot) { $arguments += @('--data-root', $DataRoot) }
        Invoke-Python @arguments
    }
    'Fixed' {
        Assert-Release
        Assert-LiveConfirmation
        if (-not $Request -or -not $CaseIndex) { throw 'Fixed requires -Request and -CaseIndex.' }
        $arguments = @((Join-Path $Root 'tools/run_product_batch.py'), '--request', $Request, '--case-index', $CaseIndex, '--keep-going')
        if ($DataRoot) { $arguments += @('--data-root', $DataRoot) }
        if ($OutputRoot) { $arguments += @('--output-root', $OutputRoot) }
        Invoke-Python @arguments
    }
    'Resume' {
        Assert-Release
        Assert-LiveConfirmation
        if (-not $Request -or -not $CaseIndex) { throw 'Resume requires -Request and -CaseIndex.' }
        $arguments = @((Join-Path $Root 'tools/run_product_batch.py'), '--request', $Request, '--case-index', $CaseIndex, '--resume', '--keep-going')
        if ($DataRoot) { $arguments += @('--data-root', $DataRoot) }
        if ($OutputRoot) { $arguments += @('--output-root', $OutputRoot) }
        Invoke-Python @arguments
    }
    'Adaptive' {
        Assert-Release -Required
        Assert-LiveConfirmation
        if (-not $CampaignDir -or -not $CampaignId -or -not $CaseIndex -or -not $RunRoot) {
            throw 'Adaptive requires -CampaignDir, -CampaignId, -CaseIndex, and -RunRoot.'
        }
        $manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
        $arguments = @((Join-Path $Root 'tools/run_adaptive.py'), 'init', '--campaign-dir', $CampaignDir, '--campaign-id', $CampaignId, '--release-id', $manifest.release_id, '--case-index', $CaseIndex, '--run-root', $RunRoot, '--products') + $Products
        if ($DataRoot) { $arguments += @('--data-root', $DataRoot) }
        Invoke-Python @arguments
        Invoke-Python (Join-Path $Root 'tools/run_adaptive.py') 'controller' '--config' (Join-Path $CampaignDir 'config.json')
    }
    'Audit' {
        Assert-Release
        if (-not $RunRoot -or -not $SelectionRoot) { throw 'Audit requires -RunRoot and -SelectionRoot.' }
        $arguments = @((Join-Path $Root 'tools/audit_formal.py'), '--run-root', $RunRoot, '--selection-root', $SelectionRoot)
        if ($OutputRoot) { $arguments += @('--output', $OutputRoot) }
        Invoke-Python @arguments
    }
    'Aggregate' {
        Assert-Release
        if (-not $Results -or -not $OutputRoot) { throw 'Aggregate requires -Results and -OutputRoot.' }
        $arguments = @((Join-Path $Root 'tools/aggregate_results.py')) + $Results + @('--output', $OutputRoot)
        if ($RunId) { $arguments += @('--run-id', $RunId) }
        Invoke-Python @arguments
    }
    'Export' {
        Assert-Release -Required
        if (-not $Destination) { throw 'Export requires -Destination.' }
        Invoke-Python (Join-Path $Root 'tools/release_manifest.py') 'export' '--root' $Root '--manifest' $ManifestPath '--destination' $Destination
    }
}
