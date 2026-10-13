$ErrorActionPreference = "Stop"

. (Join-Path (Split-Path -Parent $PSScriptRoot) "tools/windows_dpapi_credential.ps1")

$testPath = Join-Path $env:TEMP "federated-asr-dpapi-roundtrip-$PID.tmp"
$sample = "roundtrip-sample-123"
try {
    $secure = ConvertTo-SecureString $sample -AsPlainText -Force
    Save-FederatedAsrCredential -Secret $secure -Path $testPath
    $onDisk = [IO.File]::ReadAllText($testPath, [Text.Encoding]::UTF8)
    if ($onDisk.Contains($sample)) {
        throw "DPAPI credential file contains the plaintext sample."
    }
    $loaded = Get-FederatedAsrCredential -Path $testPath
    $roundTrip = Convert-FederatedSecureStringToPlainText -Secret $loaded
    if ($roundTrip -ne $sample) {
        throw "DPAPI credential round trip failed."
    }
    [pscustomobject]@{
        round_trip = $true
        plaintext_on_disk = $false
        encrypted_length = $onDisk.Trim().Length
    } | ConvertTo-Json
}
finally {
    if (Test-Path -LiteralPath $testPath) {
        Remove-Item -LiteralPath $testPath -Force
    }
}
