"""Exercise capture selection in dry-run mode with synthetic OS metadata only."""
from pathlib import Path
import shutil
import subprocess
import unittest


ENGINES = tuple(path for name in ("powershell", "pwsh") if (path := shutil.which(name)))


@unittest.skipUnless(ENGINES, "PowerShell required")
class CareerCaptureScopeTest(unittest.TestCase):
    def test_dry_run_tls_selection_and_process_guard(self):
        script = Path(__file__).parents[1] / "scripts" / "capture-career.ps1"
        command = r"""
$ErrorActionPreference = 'Stop'
function Get-Process { param($Name, $ErrorAction) [pscustomobject]@{ Id = 12345 } }
function Get-NetTCPConnection {
    param($OwningProcess, $State)
    if ($OwningProcess -ne 12345 -or $State -ne 'Established') { throw 'Wrong process query.' }
    @(
        [pscustomobject]@{RemoteAddress='192.0.2.1'; RemotePort=443; LocalPort=50001},
        [pscustomobject]@{RemoteAddress='192.0.2.2'; RemotePort=12101; LocalPort=50002},
        [pscustomobject]@{RemoteAddress='127.0.0.1'; RemotePort=443; LocalPort=50003},
        [pscustomobject]@{RemoteAddress='127.0.0.2'; RemotePort=443; LocalPort=50004},
        [pscustomobject]@{RemoteAddress='::1'; RemotePort=443; LocalPort=50005}
    )
}
# Any escape from dry-run mode must fail before OS/filter/file mutations.
function pktmon.exe { throw 'Unexpected Packet Monitor call.' }
function logman.exe { throw 'Unexpected trace-session call.' }
function New-Item { throw 'Unexpected filesystem mutation.' }
$normal = & '__SCRIPT__' -DryRun -ExpectedGameProcessId 12345 6>&1 | Out-String
if ($normal -notmatch 'found 2 game TCP connections') { throw 'Default selection regression.' }
$tls = & '__SCRIPT__' -DryRun -TlsOnly -ExpectedGameProcessId 12345 6>&1 | Out-String
if ($tls -notmatch 'found 1 game TCP connections') { throw 'TLS selection regression.' }
if ($tls -notmatch 'Excluded established remote game connections: 1') { throw 'Missing excluded connection warning.' }
$all = & '__SCRIPT__' -DryRun -AllComponents -ExpectedGameProcessId 12345 6>&1 | Out-String
if ($all -notmatch 'found 2 game TCP connections' -or $all -notmatch 'Capture components: all') { throw 'Component selection changed endpoint scope.' }
$rejected = $false
try { & '__SCRIPT__' -DryRun -TlsOnly -ExpectedGameProcessId 12346 } catch {
    if ($_.Exception.Message -notlike 'The game process changed.*') { throw }
    $rejected = $true
}
if (-not $rejected) { throw 'Changed game process accepted.' }
function Get-NetTCPConnection {
    param($OwningProcess, $State)
    [pscustomobject]@{RemoteAddress='192.0.2.2'; RemotePort=12101; LocalPort=50002}
}
function Start-Sleep { throw 'Dry run must not wait.' }
$emptyTls = & '__SCRIPT__' -DryRun -TlsOnly -AllComponents -FollowConnections -ExpectedGameProcessId 12345 6>&1 | Out-String
if ($emptyTls -notmatch 'found 0 game TCP connections' -or $emptyTls -notmatch 'Would wait up to 30 seconds') { throw 'Zero-connection follow preview regression.' }
$rejected = $false
try { & '__SCRIPT__' -DryRun -TlsOnly -ExpectedGameProcessId 12345 } catch {
    if ($_.Exception.Message -notlike 'Expected 1-16 established game TCP connections*') { throw }
    $rejected = $true
}
if (-not $rejected) { throw 'Zero-connection fixed capture accepted.' }
function Get-NetTCPConnection {
    param($OwningProcess, $State)
    1..17 | ForEach-Object { [pscustomobject]@{RemoteAddress="192.0.2.$_"; RemotePort=443; LocalPort=(50000+$_)} }
}
$rejected = $false
try { & '__SCRIPT__' -DryRun -FollowConnections -ExpectedGameProcessId 12345 } catch {
    if ($_.Exception.Message -notlike 'Expected 1-16 established game TCP connections*') { throw }
    $rejected = $true
}
if (-not $rejected) { throw 'Too many bidirectional filters accepted.' }
Write-Output 'PASS: dry-run scope, loopback exclusion and process mismatch; no capture.'
""".replace("__SCRIPT__", str(script).replace("'", "''"))
        for engine in ENGINES:
            with self.subTest(engine=engine):
                result = subprocess.run(
                    [engine, "-NoProfile", "-NonInteractive", "-Command", command],
                    capture_output=True, text=True, timeout=20,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("PASS:", result.stdout)

    def test_packet_counter_distinguishes_empty_unreadable_and_nonempty(self):
        script = Path(__file__).parents[1] / "scripts" / "capture-career.ps1"
        command = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile('__SCRIPT__', [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Invalid capture script syntax.' }
$fn = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Get-CareerPacketEventCount' }, $true)
function Get-WinEvent {
    [CmdletBinding()]param($Path, [switch]$Oldest)
    if ($Path -ne 'synthetic.etl' -or -not $Oldest) { throw 'Unexpected event query.' }
    if ($global:fixtureMode -eq 'unreadable') { throw 'Fixture read failure.' }
    if ($global:fixtureMode -eq 'noevents') {
        $PSCmdlet.ThrowTerminatingError([Management.Automation.ErrorRecord]::new(
            [Exception]::new('Synthetic empty log'), 'NoMatchingEventsFound',
            [Management.Automation.ErrorCategory]::ObjectNotFound, $null))
    }
    # Foreign-provider IDs and PktMon counters must not be counted as packets.
    [pscustomobject]@{ProviderName='Unrelated'; Id=160; Message='SYNTHETIC_PACKET_MUST_NOT_BE_OUTPUT'}
    if ($global:fixtureMode -eq 'unrecognized') { return }
    [pscustomobject]@{ProviderName='Microsoft-Windows-PktMon'; Id=90; Message='SYNTHETIC_PACKET_MUST_NOT_BE_OUTPUT'}
    if ($global:fixtureMode -eq 'empty') { return }
    @(160, 170, 160) | ForEach-Object {
        [pscustomobject]@{ProviderName='Microsoft-Windows-PktMon'; Id=$_; Message='SYNTHETIC_PACKET_MUST_NOT_BE_OUTPUT'}
    }
    if ($global:fixtureMode -eq 'partial') { throw 'Synthetic failure after reading some packets.' }
}
foreach ($mode in @('empty', 'unreadable', 'noevents', 'unrecognized', 'partial', 'nonempty')) {
    $global:fixtureMode = $mode
    $result = & $fn.Body.GetScriptBlock() -CapturePath 'synthetic.etl'
    if ($mode -eq 'empty' -and (-not $result.verified -or $result.count -ne 0)) { throw 'Empty count regression.' }
    if ($mode -eq 'nonempty' -and (-not $result.verified -or $result.count -ne 3)) { throw 'Nonempty count regression.' }
    if ($mode -in @('unreadable', 'noevents', 'unrecognized', 'partial') -and ($result.verified -or $null -ne $result.count)) { throw 'Unreadable/undecoded log treated as empty.' }
    if (($result | ConvertTo-Json) -match 'SYNTHETIC_PACKET') { throw 'Packet data leaked.' }
}
Write-Output 'PASS: empty, unreadable and nonempty event counts; no packet output.'
""".replace("__SCRIPT__", str(script).replace("'", "''"))
        self.assert_passes_in_engines(command)

    def test_mocked_capture_reports_empty_and_changed_connections(self):
        self.run_mocked_capture("fixed")

    def test_follow_adds_only_new_game_tuple_in_both_directions(self):
        self.run_mocked_capture("follow")

    def test_follow_filter_failure_stops_and_cleans_partial_pair(self):
        self.run_mocked_capture("follow_failure")

    def test_follow_filter_limit_stops_without_unfiltered_capture(self):
        self.run_mocked_capture("limit")

    def test_follow_game_process_change_stops_before_another_query(self):
        self.run_mocked_capture("game_changed")

    def test_tls_follow_waits_for_connection_and_ignores_non_tls_burst(self):
        self.run_mocked_initial_wait("connected")

    def test_initial_wait_timeout_does_not_start_capture_or_add_filters(self):
        self.run_mocked_initial_wait("timeout")

    def test_initial_wait_process_change_does_not_query_replacement(self):
        self.run_mocked_initial_wait("changed")

    def test_initial_wait_rechecks_existing_capture_before_mutation(self):
        self.run_mocked_initial_wait("busy")

    def run_mocked_initial_wait(self, mode):
        script = Path(__file__).parents[1] / "scripts" / "capture-career.ps1"
        command = r"""
$ErrorActionPreference = 'Stop'
$source = Get-Content -LiteralPath '__SCRIPT__' -Raw
$guard = 'if (-not $capturePrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))'
if (-not $source.Contains($guard)) { throw 'Elevation fixture needs updating.' }
$source = $source.Replace($guard, 'if ($false)')
$source = $source.Replace('$connectionWaitTimer = [Diagnostics.Stopwatch]::StartNew()', '$connectionWaitTimer = $global:waitClock')
$source = $source.Replace('$captureTimer = [Diagnostics.Stopwatch]::StartNew()', '$captureTimer = $global:captureClock')
$global:waitClock = [pscustomobject]@{Elapsed=[TimeSpan]::Zero}
$global:captureClock = [pscustomobject]@{Elapsed=[TimeSpan]::Zero}
$global:waitMode = '__MODE__'
$global:recording = $false
$global:fixtureReport = $null
$global:mutations = [Collections.Generic.List[string]]::new()
function Get-Process {
    param($Name, $ErrorAction)
    $gameId = if ($global:waitMode -eq 'changed' -and $global:waitClock.Elapsed.TotalSeconds -gt 0) {54321} else {12345}
    [pscustomobject]@{Id=$gameId}
}
function Get-NetTCPConnection {
    param($OwningProcess, $State)
    if ($OwningProcess -ne 12345 -or $State -ne 'Established') { throw 'Wrong connection scope.' }
    if ($global:waitMode -eq 'changed' -and $global:waitClock.Elapsed.TotalSeconds -gt 0) { throw 'Queried after process change.' }
    [pscustomobject]@{LocalAddress='192.0.2.10'; LocalPort=50002; RemoteAddress='192.0.2.2'; RemotePort=12101}
    if ($global:waitClock.Elapsed.TotalSeconds -gt 0) {
        # More than 16 unrelated game connections must not consume TLS slots.
        1..20 | ForEach-Object {
            [pscustomobject]@{LocalAddress='192.0.2.10'; LocalPort=(51000+$_); RemoteAddress="192.0.2.$_"; RemotePort=8880}
        }
        if ($global:waitMode -ne 'timeout') {
            [pscustomobject]@{LocalAddress='192.0.2.10'; LocalPort=50001; RemoteAddress='192.0.2.1'; RemotePort=443}
            if ($global:recording) {
                [pscustomobject]@{LocalAddress='192.0.2.10'; LocalPort=50003; RemoteAddress='192.0.2.3'; RemotePort=443}
            }
        }
    }
}
function Start-Sleep {
    param($Milliseconds)
    if ($global:recording) { $global:captureClock.Elapsed += [TimeSpan]::FromMilliseconds($Milliseconds) }
    else { $global:waitClock.Elapsed += [TimeSpan]::FromMilliseconds($Milliseconds) }
}
function logman.exe {
    $global:LASTEXITCODE=0
    if ($global:waitMode -eq 'busy') { 'PktMon Trace Running' } else { 'No PktMon session' }
}
function pktmon.exe {
    $global:LASTEXITCODE=0
    if (($args -join ' ') -eq 'filter list') { 'Packet Filters: None'; return }
    $global:mutations.Add(($args -join ' '))
    if ($args[0] -eq 'start') { $global:recording=$true }
}
function New-Item { param($ItemType, $Path) $global:mutations.Add('new-folder') }
function Set-Content {
    param([Parameter(ValueFromPipeline)]$Value, $LiteralPath, $Encoding)
    process { $global:fixtureReport = $Value | ConvertFrom-Json }
}
function Get-WinEvent {
    [CmdletBinding()]param($Path, [switch]$Oldest)
    [pscustomobject]@{ProviderName='Microsoft-Windows-PktMon'; Id=20}
}
$failure = $null
try {
    $output = & ([scriptblock]::Create($source)) -TlsOnly -AllComponents -FollowConnections -ExpectedGameProcessId 12345 3>&1 6>&1 | Out-String
} catch { $failure = $_.Exception.Message }
if ($global:waitMode -ne 'connected') {
    $expected = switch ($global:waitMode) {
        'timeout' { 'No eligible game connection appeared within 30 seconds.*' }
        'changed' { 'The game process changed while waiting.*' }
        'busy' { 'Cannot confirm that Packet Monitor is idle.*' }
    }
    if (-not $failure -or $failure -notlike $expected -or $global:mutations.Count -ne 0 -or $null -ne $global:fixtureReport) {
        throw 'Initial wait failure did not leave capture/filesystem untouched.'
    }
    if ($global:waitMode -eq 'timeout' -and $global:waitClock.Elapsed.TotalSeconds -ne 30) { throw 'Wait was not bounded.' }
} else {
    if ($failure) { throw $failure }
    $r = $global:fixtureReport
    if ($r.schema_version -ne 4 -or -not $r.waited_for_initial_connection -or -not $r.tls_only -or
        -not $r.follow_connections -or $r.selected_connections.Count -ne 2 -or $r.connections_added_during_capture -ne 1 -or
        $r.initially_excluded_connections -ne 21 -or $r.scope_refresh_failure -or $r.game_changed -or
        $r.career_access_verified -or $r.new_connection_coverage_guaranteed -or $null -ne $r.new_connections_captured -or
        $global:captureClock.Elapsed.TotalSeconds -ne 30) { throw 'Waited TLS follow report regression.' }
    $filters = @($global:mutations | Where-Object { $_ -like 'filter add *' })
    if ($filters.Count -ne 4 -or @($filters | Where-Object { $_ -match '8880|12101' }).Count -ne 0 -or
        $filters -notcontains 'filter add MAKCareer1Out -t TCP -i 192.0.2.10 192.0.2.1 -p 50001 443' -or
        $filters -notcontains 'filter add MAKCareer1In -t TCP -i 192.0.2.1 192.0.2.10 -p 443 50001' -or
        $filters -notcontains 'filter add MAKCareer2Out -t TCP -i 192.0.2.10 192.0.2.3 -p 50003 443' -or
        $filters -notcontains 'filter add MAKCareer2In -t TCP -i 192.0.2.3 192.0.2.10 -p 443 50003') { throw 'TLS wait/follow filters broadened.' }
    $startIndex = @($global:mutations | Where-Object { $_ -like 'start *' })
    if ($startIndex.Count -ne 1 -or $global:mutations.IndexOf($startIndex[0]) -le $global:mutations.IndexOf($filters[1]) -or
        $global:mutations.IndexOf('filter remove') -le $global:mutations.IndexOf('stop')) { throw 'Unfiltered start or cleanup regression.' }
    if ($output -notmatch 'WAITING FOR GAME CONNECTION' -or $output -notmatch 'Repeat the Career/tab change now') { throw 'Wait/repeat instructions missing.' }
}
Write-Output 'PASS: bounded initial connection wait and game-only HTTPS follow lifecycle.'
""".replace("__SCRIPT__", str(script).replace("'", "''")).replace("__MODE__", mode)
        self.assert_passes_in_engines(command)

    def run_mocked_capture(self, mode):
        script = Path(__file__).parents[1] / "scripts" / "capture-career.ps1"
        # Execute the real lifecycle with mocked commands. Only the elevation
        # condition and clock are substituted; no process/filter/file is changed.
        command = r"""
$ErrorActionPreference = 'Stop'
$source = Get-Content -LiteralPath '__SCRIPT__' -Raw
$guard = 'if (-not $capturePrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))'
if (-not $source.Contains($guard)) { throw 'Elevation fixture needs updating.' }
$source = $source.Replace($guard, 'if ($false)')
$source = $source.Replace('[Diagnostics.Stopwatch]::StartNew()', '$global:fixtureClock')
$global:fixtureClock = [pscustomobject]@{Elapsed=[TimeSpan]::Zero}
$global:fixtureCalls = [Collections.Generic.List[string]]::new()
$global:fixtureReport = $null
$global:fixtureMode = '__MODE__'
function Get-Process {
    param($Name, $ErrorAction)
    $gameId = if ($global:fixtureMode -eq 'game_changed' -and $global:fixtureClock.Elapsed.TotalSeconds -gt 0) {54321} else {12345}
    [pscustomobject]@{Id=$gameId}
}
function Get-NetTCPConnection {
    param($OwningProcess, $State)
    if ($OwningProcess -ne 12345 -or $State -ne 'Established') { throw 'Wrong connection scope.' }
    $port = if ($global:fixtureClock.Elapsed.TotalSeconds -eq 0) { 50001 } else { 50003 }
    [pscustomobject]@{LocalAddress='192.0.2.10'; LocalPort=$port; RemoteAddress='192.0.2.1'; RemotePort=443}
    [pscustomobject]@{LocalAddress='192.0.2.10'; LocalPort=50002; RemoteAddress='192.0.2.2'; RemotePort=12101}
    if ($global:fixtureMode -eq 'limit') {
        3..16 | ForEach-Object {
            [pscustomobject]@{LocalAddress='192.0.2.10'; LocalPort=(50010+$_); RemoteAddress="192.0.2.$_"; RemotePort=443}
        }
    }
}
function Start-Sleep { param($Milliseconds) $global:fixtureClock.Elapsed += [TimeSpan]::FromMilliseconds($Milliseconds) }
function logman.exe { $global:LASTEXITCODE=0; 'No PktMon session' }
function pktmon.exe {
    $global:fixtureCalls.Add(($args -join ' '))
    $global:LASTEXITCODE=0
    if ($global:fixtureMode -eq 'follow_failure' -and ($args -join ' ') -like 'filter add MAKCareer3In *') {
        $global:LASTEXITCODE=1
    }
    if (($args -join ' ') -eq 'filter list') { 'Packet Filters: None' }
}
function New-Item { param($ItemType, $Path) }
function Set-Content {
    param([Parameter(ValueFromPipeline)]$Value, $LiteralPath, $Encoding)
    process {
        if ($LiteralPath -notlike '*\capture-report.json') { throw 'Unexpected output file.' }
        $global:fixtureReport = $Value | ConvertFrom-Json
    }
}
function Get-WinEvent {
    [CmdletBinding()]param($Path, [switch]$Oldest)
    [pscustomobject]@{ProviderName='Microsoft-Windows-PktMon'; Id=20}
}
$follow = $global:fixtureMode -ne 'fixed'
$output = & ([scriptblock]::Create($source)) -AllComponents -FollowConnections:$follow -ExpectedGameProcessId 12345 3>&1 6>&1 | Out-String
$r = $global:fixtureReport
if ($null -eq $r -or $r.outcome -ne 'no_packets_captured' -or -not $r.packet_count_verified -or $r.packet_event_count -ne 0) {
    throw 'Capture result regression.'
}
$expected = switch ($global:fixtureMode) {
    'fixed' { @{selected=2; added=0; observations=7; outside=1; missing=1; filters=4; failure=$null; changed=$false} }
    'follow' { @{selected=3; added=1; observations=61; outside=1; missing=1; filters=6; failure=$null; changed=$false} }
    'follow_failure' { @{selected=2; added=0; observations=2; outside=1; missing=1; filters=6; failure='connection_filter_refresh_failed'; changed=$false} }
    'limit' { @{selected=16; added=0; observations=2; outside=1; missing=1; filters=32; failure='connection_filter_limit_reached'; changed=$false} }
    'game_changed' { @{selected=2; added=0; observations=1; outside=0; missing=0; filters=4; failure=$null; changed=$true} }
}
if ($r.schema_version -ne 4 -or $r.capture_components -ne 'all' -or -not $r.explicit_reverse_filters -or $r.waited_for_initial_connection -or
    $r.follow_connections -ne $follow -or $r.tls_only -or $r.selected_connections.Count -ne $expected.selected -or
    $r.initially_excluded_connections -ne 0 -or $r.connections_added_during_capture -ne $expected.added -or
    $r.unselected_connections_observed -ne $expected.outside -or $r.selected_connections_later_missing -ne $expected.missing -or
    $r.observations.Count -ne $expected.observations -or $r.scope_refresh_failure -cne $expected.failure -or
    $r.game_changed -ne $expected.changed -or $r.new_connections_captured -or
    $r.new_connection_coverage_guaranteed -or $r.career_access_verified) { throw 'Scope report regression.' }
if ($follow -and $null -ne $r.new_connections_captured) { throw 'Claimed unverified new-connection capture.' }
if ($output -notmatch 'NO PACKETS CAPTURED' -or $output -notmatch 'not a UID or authentication failure') { throw 'Empty capture hidden.' }
$calls = $global:fixtureCalls
if (@($calls | Where-Object { $_ -like 'filter add *' }).Count -ne $expected.filters -or
    $calls -notcontains 'filter add MAKCareer1Out -t TCP -i 192.0.2.10 192.0.2.1 -p 50001 443' -or
    $calls -notcontains 'filter add MAKCareer1In -t TCP -i 192.0.2.1 192.0.2.10 -p 443 50001' -or
    $calls -notcontains 'filter add MAKCareer2Out -t TCP -i 192.0.2.10 192.0.2.2 -p 50002 12101' -or
    $calls -notcontains 'filter add MAKCareer2In -t TCP -i 192.0.2.2 192.0.2.10 -p 12101 50002') { throw 'Capture filters broadened.' }
if ($global:fixtureMode -in @('follow','follow_failure')) {
    if ($calls -notcontains 'filter add MAKCareer3Out -t TCP -i 192.0.2.10 192.0.2.1 -p 50003 443' -or
        $calls -notcontains 'filter add MAKCareer3In -t TCP -i 192.0.2.1 192.0.2.10 -p 443 50003') { throw 'Missing late exact reverse-paired filter.' }
} elseif (@($calls | Where-Object { $_ -like '*50003*' }).Count -ne 0) { throw 'Unexpected late filter.' }
if ($calls.IndexOf('stop') -lt 0 -or $calls.IndexOf('filter remove') -le $calls.IndexOf('stop') -or
    $calls[$calls.Count - 1] -notlike 'etl2pcap *') { throw 'Capture cleanup order regression.' }
if (@($calls | Where-Object { $_ -like 'start --capture --comp all *' }).Count -ne 1) { throw 'Requested component selection ignored.' }
Write-Output 'PASS: full mocked capture, fixed tuples, cleanup, empty result and connection changes.'
""".replace("__SCRIPT__", str(script).replace("'", "''")).replace("__MODE__", mode)
        self.assert_passes_in_engines(command)

    def assert_passes_in_engines(self, command):
        for engine in ENGINES:
            with self.subTest(engine=engine):
                result = subprocess.run(
                    [engine, "-NoProfile", "-NonInteractive", "-Command", command],
                    capture_output=True, text=True, timeout=20,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("PASS:", result.stdout)
                self.assertNotIn("SYNTHETIC_PACKET_MUST_NOT_BE_OUTPUT", result.stdout)


if __name__ == "__main__":
    unittest.main()
