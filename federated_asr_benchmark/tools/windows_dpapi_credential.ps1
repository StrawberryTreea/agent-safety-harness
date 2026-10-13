$ErrorActionPreference = "Stop"

function Get-FederatedAsrCredentialPath {
    param([string]$Name = "deepseek_api_key")

    if (-not $IsWindows -and $PSVersionTable.PSEdition -eq "Core") {
        throw "Windows DPAPI credential storage is available only on Windows."
    }
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        throw "LOCALAPPDATA is unavailable; cannot locate the user credential store."
    }
    return Join-Path $env:LOCALAPPDATA "FederatedASRBenchmark\credentials\$Name.dpapi"
}

function Save-FederatedAsrCredential {
    param(
        [Parameter(Mandatory = $true)]
        [Security.SecureString]$Secret,
        [string]$Path = (Get-FederatedAsrCredentialPath)
    )

    $parent = Split-Path -Parent $Path
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    $protected = ConvertFrom-SecureString -SecureString $Secret
    if ([string]::IsNullOrWhiteSpace($protected)) {
        throw "DPAPI did not produce an encrypted credential."
    }
    $temporary = "$Path.$PID.tmp"
    try {
        [IO.File]::WriteAllText(
            $temporary,
            $protected + [Environment]::NewLine,
            [Text.UTF8Encoding]::new($false)
        )
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}

function Get-FederatedAsrCredential {
    param([string]$Path = (Get-FederatedAsrCredentialPath))

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    $protected = [IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8).Trim()
    if ([string]::IsNullOrWhiteSpace($protected)) {
        throw "The encrypted credential file is empty: $Path"
    }
    try {
        return ConvertTo-SecureString -String $protected
    }
    catch {
        throw "The encrypted credential cannot be decrypted by the current Windows user."
    }
}

function Convert-FederatedSecureStringToPlainText {
    param(
        [Parameter(Mandatory = $true)]
        [Security.SecureString]$Secret
    )

    $pointer = [IntPtr]::Zero
    try {
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        if ($pointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        }
    }
}
