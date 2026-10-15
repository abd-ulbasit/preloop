# Intune: run using logged-on credentials, 64-bit PowerShell host.
$ErrorActionPreference = 'Stop'
$env:PRELOOP_DISABLE_TELEMETRY = 'true'
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
if ($identity.IsSystem) {
    throw 'Run in the intended app user session, not SYSTEM.'
}
& 'C:\Program Files\Preloop\preloop.exe' agents discover --inventory
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
