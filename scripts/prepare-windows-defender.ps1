#Requires -Version 5.1
# Strengthen protection only on disposable GitHub-hosted Windows runners.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. "$PSScriptRoot/test-windows-defender.ps1"

function Enable-WindowsDefenderEventLog {
  $log = New-Object System.Diagnostics.Eventing.Reader.EventLogConfiguration 'Microsoft-Windows-Windows Defender/Operational'
  try {
    $log.IsEnabled = $true
    $log.SaveChanges()
  } finally { $log.Dispose() }
}

function Update-GitHubHostedDefenderSignature {
  # The hosted image can briefly lose Defender's CIM RPC connection while
  # protection starts. Retry only that transport failure, never policy errors.
  for ($attempt = 1; $attempt -le 3; $attempt++) {
    try {
      Update-MpSignature -ErrorAction Stop
      return
    } catch {
      if ($_.FullyQualifiedErrorId -notmatch '^HRESULT 0x800706be(?:,|$)' -or $attempt -eq 3) {
        throw
      }
      Write-Warning "Defender signature update RPC failed; retrying ($attempt/3)."
      Start-Sleep -Seconds (2 * $attempt)
    }
  }
}

function Initialize-GitHubHostedDefender {
  if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or $env:OS -ne 'Windows_NT') {
    throw 'Protection setup is restricted to disposable GitHub-hosted Windows runners.'
  }
  # Hosted build images disable protection and exclude build drives by default.
  # Reverse those settings before any release executables are downloaded.
  Start-Service -Name WinDefend -ErrorAction Stop
  Set-MpPreference -DisableRealtimeMonitoring $false -DisableBehaviorMonitoring $false `
    -DisableIOAVProtection $false -DisableScriptScanning $false -DisableBlockAtFirstSeen $false `
    -MAPSReporting Advanced -SubmitSamplesConsent SendSafeSamples -ErrorAction Stop
  $preferences = Get-MpPreference -ErrorAction Stop
  foreach ($name in @('ExclusionPath', 'ExclusionExtension', 'ExclusionProcess', 'ExclusionIpAddress')) {
    $values = @($preferences.$name | Where-Object { $_ })
    if ($values.Count -gt 0) {
      $parameters = @{ ErrorAction = 'Stop' }
      $parameters[$name] = $values
      Remove-MpPreference @parameters
    }
  }
  Enable-WindowsDefenderEventLog
  Update-GitHubHostedDefenderSignature
  Assert-DefenderCoverage | Format-List AMRunningMode, AMEngineVersion, AMProductVersion, AntivirusSignatureVersion, RealTimeProtectionEnabled
}

if ($MyInvocation.InvocationName -ne '.') { Initialize-GitHubHostedDefender }
