"""Exercise the live-game lookup recorder without starting a real recorder."""
from pathlib import Path
import shutil
import subprocess
import unittest

SCRIPT = Path(__file__).parents[1] / "scripts" / "capture-player-career.ps1"
ENGINES = tuple(p for name in ("powershell", "pwsh") if (p := shutil.which(name)))


@unittest.skipUnless(ENGINES, "PowerShell required")
class PlayerCareerCaptureTest(unittest.TestCase):
    def test_preview_guards_and_named_cleanup(self):
        for mode in ("preview", "unreviewed", "changed_scope", "wrong_game",
                     "wrong_peer", "not_admin", "busy", "record",
                     "start_failure", "stop_failure", "game_exit", "startup_record",
                     "startup_game_present", "startup_game_exit"):
            for engine in ENGINES:
                with self.subTest(mode=mode, engine=engine):
                    script = FIXTURE.replace("__SCRIPT__", str(SCRIPT).replace("'", "''")).replace("__MODE__", mode)
                    result = subprocess.run([engine, "-NoProfile", "-NonInteractive", "-Command", script],
                                            capture_output=True, text=True, timeout=30)
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
$global:testingRecord=$false
$global:startupMode='__MODE__'.StartsWith('startup_')
function Get-Process {
    param($Name,$ErrorAction)
    if ($global:startupMode) {
        if ($global:testingRecord -and '__MODE__' -eq 'startup_game_present') {[pscustomobject]@{Id=30000}; return}
        if ($global:fixtureClock.Elapsed.TotalSeconds -eq 0 -or
            ('__MODE__' -eq 'startup_game_exit' -and $global:fixtureClock.Elapsed.TotalSeconds -gt 1)) {return}
        [pscustomobject]@{Id=30000}; return
    }
    if ($global:testingRecord -and ('__MODE__' -eq 'wrong_game' -or
        ('__MODE__' -eq 'game_exit' -and $global:fixtureClock.Elapsed.TotalSeconds -gt 0))) {return}
    [pscustomobject]@{Id=21752}
}
function Get-CimInstance {
    param($Namespace,$ClassName,$Filter,$ErrorAction)
    $expectedId=if ($global:startupMode) {30000} else {21752}
    if ($Filter -ne ('OwningProcess = {0} AND State = 5' -f $expectedId)) {throw 'Wrong process query.'}
    if ($global:testingRecord -and '__MODE__' -eq 'wrong_peer') {return}
    [pscustomobject]@{LocalAddress='192.0.2.1';LocalPort=50001;RemoteAddress='34.60.237.47';RemotePort=10203}
    [pscustomobject]@{LocalAddress='192.0.2.1';LocalPort=50002;RemoteAddress='35.236.255.68';RemotePort=12101}
    [pscustomobject]@{LocalAddress='192.0.2.1';LocalPort=50003;RemoteAddress='8.221.212.39';RemotePort=21308}
}
function logman.exe {
    $global:calls.Add('session-check'); $global:LASTEXITCODE=0
    if ('__MODE__' -eq 'busy') {'MAKPlayer123456 Trace Running'} else {'OtherSession Trace Running'}
}
function netsh.exe {
    $global:calls.Add(($args -join ' ')); $global:LASTEXITCODE=0
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
$block=[scriptblock]::Create($source)
$plan=(& $block -ExpectedGameProcessId 21752 -FromLauncher:$global:startupMode | Out-String) | ConvertFrom-Json
if ($global:calls.Count -ne 0) {throw 'Preview mutated state.'}
if (($plan.remote_ipv4_addresses -join ',') -ne '34.60.237.47,35.236.255.68' -or
    $plan.tcp_ports_restricted -ne $false -or $plan.requires_existing_game -eq $global:startupMode) {throw 'Wrong preview scope.'}
if (-not $global:startupMode -and ($plan.initial_connections.Count -ne 2 -or $plan.duration_seconds -ne 90)) {throw 'Wrong live-game preview.'}
if ($global:startupMode -and ($plan.initial_connections.Count -ne 0 -or $plan.duration_seconds -ne 180)) {throw 'Wrong startup preview.'}
$caught=$false
if ('__MODE__' -ne 'preview') {
    $global:testingRecord=$true
    $parameters=@{Record=$true;ExpectedGameProcessId=21752;RunId=('b'*32);FromLauncher=$global:startupMode}
    if ('__MODE__' -ne 'unreviewed') {
        $parameters.ReviewedScopeFingerprint=if ('__MODE__' -eq 'changed_scope') {'0'*64} else {$plan.scope_fingerprint}
    }
    try {& $block @parameters | Out-Null} catch {$caught=$true}
}
$starts=@($global:calls | Where-Object {$_ -like 'trace start *'})
$stops=@($global:calls | Where-Object {$_ -like 'trace stop *'})
if ('__MODE__' -in @('record','start_failure','stop_failure','game_exit','startup_record','startup_game_exit')) {
    if ($starts.Count -ne 1 -or $stops.Count -ne 1 -or $stops[0] -cne ('trace stop sessionname=MAKPlayer' + ('b'*16))) {throw 'Wrong named lifecycle.'}
    foreach ($required in @('capture=yes','capturetype=physical','report=disabled','persistent=no',
        'maxSize=32','fileMode=single','overwrite=no','correlation=disabled','perfMerge=no',
        'CaptureMultiLayer=no','Ethernet.Type=IPv4','Protocol=TCP',
        'IPv4.Address=(34.60.237.47,35.236.255.68)','PacketTruncateBytes=65535')) {
        if ($starts[0].Split(' ') -cnotcontains $required) {throw ('Missing restriction: ' + $required)}
    }
    if ($starts[0] -match '8\.221\.212\.39|scenario=|provider=') {throw 'Expanded scope.'}
    if ($null -eq $global:report -or $global:report.capture_scope_verified -ne $false -or
        $global:report.career_access_verified -ne $false -or
        $global:report.started -ne ('__MODE__' -ne 'start_failure') -or
        $global:report.stopped -ne ('__MODE__' -ne 'stop_failure')) {throw 'Wrong report.'}
    if ('__MODE__' -eq 'record' -and $global:report.observations.Count -ne 90) {throw 'Wrong bounded duration.'}
    if ('__MODE__' -eq 'startup_record' -and ($global:report.observations.Count -ne 180 -or
        $global:report.observations[0].game_process_id -ne 30000)) {throw 'Wrong startup lifecycle or process binding.'}
} elseif ($starts.Count -ne 0 -or $stops.Count -ne 0) {throw 'Recording ran despite a guard.'}
if ($caught -ne ('__MODE__' -notin @('preview','record','startup_record'))) {throw 'Wrong error outcome.'}
'FIXTURE PASSED'
"""
