param(
    [Parameter(Mandatory = $true)][string]$PackageDir,
    [string]$Image = "ash-parallel-cli-eval:local",
    [string]$Output = "agent-cli-runtime-image.tar"
)
$ErrorActionPreference = "Stop"
$PackageDir = (Resolve-Path $PackageDir).Path
& docker build -t $Image $PackageDir
if ($LASTEXITCODE -ne 0) { throw "Docker image build failed." }
$Output = [IO.Path]::GetFullPath($Output)
& docker save -o $Output $Image
if ($LASTEXITCODE -ne 0) { throw "Docker image export failed." }
Write-Host "Created $Output. It contains CLI runtimes only; credentials and evaluator tasks are not included."
