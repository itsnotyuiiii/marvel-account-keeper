"""Exercise the fixed-IP Netsh experiment with every OS mutation mocked."""
from pathlib import Path
import shutil
import subprocess
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts" / "capture-career-netsh.ps1"
ENGINES = tuple(p for name in ("powershell", "pwsh") if (p := shutil.which(name)))


@unittest.skipUnless(ENGINES, "PowerShell required")
class CareerNetshCaptureTest(unittest.TestCase):
    def test_preview_and_named_session_lifecycle(self):
        for mode in ("preview", "unreviewed", "changed_scope", "game_present",
                     "late_game", "not_admin", "busy", "record", "start_failure",
                     "stop_failure", "metadata_failure"):
            for engine in ENGINES:
                with self.subTest(mode=mode, engine=engine):
                    result = subprocess.run(
                        [engine, "-NoProfile", "-NonInteractive", "-Command",
                         FIXTURE.replace("__SCRIPT__", str(SCRIPT).replace("'", "''"))
                         .replace("__MODE__", mode)],
                        capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("FIXTURE PASSED", result.stdout)


FIXTURE = r"""
$ErrorActionPreference='Stop'
$source=Get-Content -LiteralPath '__SCRIPT__' -Raw
$guard='if (-not $capturePrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))'
if (-not $source.Contains($guard)) {throw 'Admin fixture marker changed.'}
$source=$source.Replace($guard, $(if ('__MODE__' -eq 'not_admin') {'if ($true)'} else {'if ($false)'}))
$source=$source.Replace('[Diagnostics.Stopwatch]::StartNew()','$global:fixtureClock')
$global:fixtureClock=[pscustomobject]@{Elapsed=[TimeSpan]::Zero}
$global:calls=[Collections.Generic.List[string]]::new()
$global:report=$null
$global:processCalls=0
function Get-Process {
    param($Name,$ErrorAction)
    $global:processCalls++
    if ('__MODE__' -eq 'game_present' -or
        ('__MODE__' -eq 'late_game' -and $global:processCalls -ge 2) -or
        $global:fixtureClock.Elapsed.TotalSeconds -gt 0) {[pscustomobject]@{Id=12345}}
}
function logman.exe {
    $global:calls.Add('session-check');$global:LASTEXITCODE=0
    if ('__MODE__' -eq 'busy') {'PktMon Trace Running'} else {'OtherSession Trace Running'}
}
function netsh.exe {
    $global:calls.Add(($args -join ' '));$global:LASTEXITCODE=0
    if (('__MODE__' -eq 'start_failure' -and $args[1] -eq 'start') -or
        ('__MODE__' -eq 'stop_failure' -and $args[1] -eq 'stop')) {$global:LASTEXITCODE=5}
}
function New-Item {param($ItemType,$Path,$ErrorAction) $global:calls.Add('create-folder')}
function Start-Transcript {param($LiteralPath) $global:calls.Add('transcript-start')}
function Stop-Transcript {$global:calls.Add('transcript-stop')}
function Set-Content {
    param($LiteralPath,$Encoding,[Parameter(ValueFromPipeline=$true)]$Value)
    process {$global:report=$Value | ConvertFrom-Json; $global:calls.Add('write-report')}
}
function Start-Sleep {param($Milliseconds) $global:fixtureClock.Elapsed += [TimeSpan]::FromMilliseconds($Milliseconds)}
function Get-CimInstance {
    param($Namespace,$ClassName,$Filter,$ErrorAction)
    if ($Filter -ne 'OwningProcess = 12345 AND State = 5') {throw 'Wrong metadata query.'}
    if ('__MODE__' -eq 'metadata_failure') {throw 'Synthetic metadata failure.'}
    [pscustomobject]@{LocalAddress='192.0.2.1';LocalPort=50000;RemoteAddress='34.60.237.47';RemotePort=10202}
}
$block=[scriptblock]::Create($source)
$plan=(& $block | Out-String) | ConvertFrom-Json
if ($global:calls.Count -ne 0 -or $global:processCalls -ne 0) {throw 'Preview changed or queried OS state.'}
if (($plan.remote_ipv4_addresses -join ',') -ne '35.236.255.68,8.221.212.39' -or
    $plan.duration_seconds -ne 90 -or $plan.tcp_ports_restricted -ne $false -or
    $plan.capture_scope_verified -ne $false) {throw 'Preview scope changed.'}
$caught=$false
if ('__MODE__' -ne 'preview') {
    $parameters=@{Record=$true;RunId=('a'*32)}
    if ('__MODE__' -ne 'unreviewed') {
        $parameters.ReviewedScopeFingerprint=if ('__MODE__' -eq 'changed_scope') {'0'*64} else {$plan.scope_fingerprint}
    }
    try {& $block @parameters | Out-Null} catch {$caught=$true}
}
$starts=@($global:calls | Where-Object {$_ -like 'trace start *'})
$stops=@($global:calls | Where-Object {$_ -like 'trace stop *'})
$captureModes=@('record','start_failure','stop_failure','metadata_failure')
if ('__MODE__' -in $captureModes) {
    if ($starts.Count -ne 1 -or $stops.Count -ne 1) {throw 'Start/stop attempt count wrong.'}
    if ($stops[0] -cne ('trace stop sessionname=MAKCareer' + ('a'*16))) {throw 'Stop was not scoped to own session.'}
    foreach ($required in @('capture=yes','capturetype=physical','report=disabled','persistent=no',
        'maxSize=32','fileMode=single','overwrite=no','correlation=disabled','perfMerge=no',
        'CaptureMultiLayer=no','Ethernet.Type=IPv4','Protocol=TCP',
        'IPv4.Address=(35.236.255.68,8.221.212.39)','PacketTruncateBytes=65535')) {
        if (($starts[0].Split(' ')) -cnotcontains $required) {throw ('Missing filter/limit: ' + $required)}
    }
    if ($starts[0] -match '34\.60\.237\.47|scenario=|provider=') {throw 'Expanded capture scope.'}
    if ($null -eq $global:report -or $global:report.capture_scope_verified -ne $false -or
        $global:report.career_access_verified -ne $false) {throw 'Missing or overstated report.'}
    if ($global:report.started -ne ('__MODE__' -ne 'start_failure') -or
        $global:report.stopped -ne ('__MODE__' -ne 'stop_failure')) {throw 'Wrong lifecycle state.'}
    if ('__MODE__' -eq 'record' -and
        ($global:report.observations.Count -ne 90 -or $global:report.game_process_ids_observed[0] -ne 12345)) {throw 'Wrong timed metadata.'}
} elseif ($starts.Count -ne 0 -or $stops.Count -ne 0) {throw 'Recording ran despite a guard.'}
if ($caught -ne ('__MODE__' -notin @('preview','record'))) {throw 'Unexpected error outcome.'}
if ('__MODE__' -in @('preview','unreviewed','changed_scope','game_present','not_admin') -and $global:calls.Count -ne 0) {throw 'Early guard mutated state.'}
'FIXTURE PASSED'
"""
