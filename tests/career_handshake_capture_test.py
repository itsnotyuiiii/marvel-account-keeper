"""Exercise the handshake-first capture with synthetic OS metadata, never traffic."""
from pathlib import Path
import shutil
import subprocess
import unittest


ENGINES = tuple(path for name in ("powershell", "pwsh") if (path := shutil.which(name)))
SCRIPT = Path(__file__).parents[1] / "scripts" / "capture-career-handshake.ps1"


@unittest.skipUnless(ENGINES, "PowerShell required")
class CareerHandshakeCaptureTest(unittest.TestCase):
    def test_live_recording_is_held_after_observed_scope_mismatch(self):
        self.run_fixture("record_disabled")

    def test_default_preview_has_no_mutations_and_discloses_peer_scope(self):
        self.run_fixture("preview")

    def test_record_requires_a_reviewed_scope_fingerprint(self):
        self.run_fixture("unreviewed")

    def test_changed_peer_scope_is_rejected_before_mutations(self):
        self.run_fixture("scope_changed")

    def test_record_uses_only_fixed_peers_and_cleans_unchanged_filters(self):
        self.run_fixture("record")

    def test_changed_filter_list_is_retained_after_stopping_our_capture(self):
        self.run_fixture("changed_filters")

    def test_process_change_stops_without_following_replacement(self):
        self.run_fixture("changed")

    def test_explicit_restart_mode_keeps_only_the_same_peer_filters(self):
        self.run_fixture("restart")

    def test_existing_filters_are_left_untouched(self):
        self.run_fixture("existing")

    def test_partial_filter_failure_never_starts_capture(self):
        self.run_fixture("filter_failure")

    def test_uncertain_start_keeps_narrow_filters_in_place(self):
        self.run_fixture("start_failure")

    def test_failed_stop_keeps_narrow_filters_in_place(self):
        self.run_fixture("stop_failure")

    def test_unreadable_count_is_not_reported_as_verified_zero(self):
        self.run_fixture("count_failure")

    def test_prior_report_preview_is_explicit_and_nonmutating(self):
        self.run_fixture("prior_preview")

    def test_prior_report_can_start_before_game_without_changing_peers(self):
        self.run_fixture("prior_record")

    def test_prior_report_changed_endpoints_are_rejected(self):
        self.run_fixture("prior_changed_peer")

    def test_prior_report_stale_scope_is_rejected(self):
        self.run_fixture("prior_stale")

    def test_prior_report_does_not_substitute_for_a_running_game(self):
        self.run_fixture("prior_game_present")

    def test_prior_report_requires_the_same_local_interface(self):
        self.run_fixture("prior_changed_local")

    def run_fixture(self, mode):
        command = r"""
$ErrorActionPreference='Stop'
$source=Get-Content -LiteralPath '__SCRIPT__' -Raw
# Lifecycle tests below mock every capture operation. Only the hold test keeps
# the actual live-recording prohibition; no fixture ever runs native PktMon.
$hold='if ($Record) { # LIVE_CAPTURE_SCOPE_HOLD'
if (-not $source.Contains($hold)) {throw 'Capture hold fixture needs updating.'}
if ('__MODE__' -ne 'record_disabled') {$source=$source.Replace($hold,'if ($false) { # LIVE_CAPTURE_SCOPE_HOLD')}
$guard='if (-not $capturePrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))'
if (-not $source.Contains($guard)) { throw 'Administrator fixture needs updating.' }
$source=$source.Replace($guard,'if ($false)')
$source=$source.Replace('[Diagnostics.Stopwatch]::StartNew()','$global:fixtureClock')
$global:fixtureClock=[pscustomobject]@{Elapsed=[TimeSpan]::Zero}
$global:mode='__MODE__'
$global:priorMode=$global:mode.StartsWith('prior_')
$global:calls=[Collections.Generic.List[string]]::new()
$global:filterNames=[Collections.Generic.List[string]]::new()
$global:report=$null
function Get-Process {
    param($Name,$ErrorAction)
    if ($global:priorMode -and $global:mode -ne 'prior_game_present' -and $global:fixtureClock.Elapsed.TotalSeconds -eq 0) {return}
    if ($global:mode -eq 'restart' -and $global:fixtureClock.Elapsed.TotalSeconds -eq 0.5) {return}
    $gameId=if (($global:mode -in @('changed','restart') -or $global:priorMode) -and $global:fixtureClock.Elapsed.TotalSeconds -gt 0) {999} else {12345}
    [pscustomobject]@{Id=$gameId}
}
function Get-NetTCPConnection {
    param($OwningProcess,$State)
    if ($global:priorMode -and $global:fixtureClock.Elapsed.TotalSeconds -eq 0) {throw 'Queried a historical PID as current.'}
    if (($OwningProcess -ne 12345 -and -not (($global:mode -eq 'restart' -or $global:priorMode) -and $OwningProcess -eq 999)) -or $State -ne 'Established') { throw 'Wrong game connection query.' }
    if ($global:mode -eq 'changed' -and $global:fixtureClock.Elapsed.TotalSeconds -gt 0) { throw 'Queried after process change.' }
    $localPort=if ($global:fixtureClock.Elapsed.TotalSeconds -eq 0) {50001} else {50002}
    @(
      [pscustomobject]@{LocalAddress='192.0.2.10';LocalPort=$localPort;RemoteAddress='192.0.2.1';RemotePort=10200},
      [pscustomobject]@{LocalAddress='192.0.2.10';LocalPort=50003;RemoteAddress='192.0.2.1';RemotePort=10200},
      [pscustomobject]@{LocalAddress='192.0.2.10';LocalPort=50004;RemoteAddress='192.0.2.2';RemotePort=12101},
      [pscustomobject]@{LocalAddress='192.0.2.10';LocalPort=50005;RemoteAddress='192.0.2.3';RemotePort=21308},
      [pscustomobject]@{LocalAddress='192.0.2.10';LocalPort=50006;RemoteAddress='192.0.2.4';RemotePort=443},
      [pscustomobject]@{LocalAddress='127.0.0.1';LocalPort=50007;RemoteAddress='127.0.0.2';RemotePort=10200}
    )
    if ($global:fixtureClock.Elapsed.TotalSeconds -gt 0 -or $global:mode -eq 'scope_changed') {
      [pscustomobject]@{LocalAddress='192.0.2.10';LocalPort=50008;RemoteAddress='192.0.2.99';RemotePort=10200}
    }
}
function Start-Sleep {param($Milliseconds) $global:fixtureClock.Elapsed += [TimeSpan]::FromMilliseconds($Milliseconds)}
function logman.exe { $global:calls.Add('session-check');$global:LASTEXITCODE=0;'No existing session' }
function pktmon.exe {
    $text=$args -join ' '
    $global:calls.Add($text)
    $global:LASTEXITCODE=0
    if ($text -eq 'filter list') {
      if ($global:mode -eq 'existing') {'OtherUserFilter'}
      elseif ($global:filterNames.Count) {'Packet Filters: '+($global:filterNames -join ',')}
      else {'Packet Filters: None'}
    }
    if ($global:mode -eq 'filter_failure' -and $text -like 'filter add MAKHello*1In *') {$global:LASTEXITCODE=5}
    if ($args[0] -eq 'filter' -and $args[1] -eq 'add' -and $global:LASTEXITCODE -eq 0) {$global:filterNames.Add($args[2])}
    if ($global:mode -eq 'start_failure' -and $args[0] -eq 'start') {$global:LASTEXITCODE=5}
    if ($global:mode -eq 'stop_failure' -and $args[0] -eq 'stop') {$global:LASTEXITCODE=5}
    if ($global:mode -eq 'changed_filters' -and $args[0] -eq 'stop') {$global:filterNames.Add('OtherUserFilter')}
}
function New-Item {param($ItemType,$Path) $global:calls.Add('create-folder')}
function Get-Item {param($LiteralPath) [pscustomobject]@{Length=4096}}
function Get-NetIPAddress {param($ErrorAction) [pscustomobject]@{IPAddress='192.0.2.10'}}
function Get-Content {
    param($LiteralPath,[switch]$Raw)
    if (-not $global:priorMode -or -not $Raw -or $LiteralPath -notlike '*\capture-report.json') {throw 'Unexpected report read.'}
    $global:priorReportJson
}
function Set-Content {
    param([Parameter(ValueFromPipeline)]$Value,$LiteralPath,$Encoding)
    process {
      if ($LiteralPath -notlike '*\capture-report.json') {throw 'Unexpected output path.'}
      $global:report=$Value | ConvertFrom-Json
    }
}
function Get-WinEvent {
    [CmdletBinding()]param($Path,[switch]$Oldest)
    if (-not $Oldest) {throw 'Expected bounded ETL enumeration.'}
    if ($global:mode -eq 'count_failure') {throw 'Synthetic unreadable trace.'}
    [pscustomobject]@{ProviderName='Microsoft-Windows-PktMon';Id=20;Message='SYNTHETIC_SECRET_DO_NOT_OUTPUT'}
    [pscustomobject]@{ProviderName='Microsoft-Windows-PktMon';Id=160;Message='SYNTHETIC_SECRET_DO_NOT_OUTPUT'}
}
$failure=$null
$options=@{ExpectedGameProcessId=12345;DurationSeconds=2;Record=($global:mode -notin @('preview','prior_preview'));AllowGameRestart=($global:mode -eq 'restart' -or $global:priorMode)}
if ($global:mode -notin @('preview','unreviewed')) {
    $header='v1|12345|2|'+[string]$options.AllowGameRestart
    $parts=@($header,'192.0.2.10|192.0.2.1|10200','192.0.2.10|192.0.2.2|12101','192.0.2.10|192.0.2.3|21308')
    $hasher=[Security.Cryptography.SHA256]::Create()
    try {$options.ReviewedScopeFingerprint=[BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes([string]::Join([char]10,$parts)))).Replace('-','').ToLowerInvariant()}
    finally {$hasher.Dispose()}
}
if ($global:priorMode) {
    $options.PriorCaptureReport=Join-Path $env:LOCALAPPDATA 'MarvelAccountKeeper\diagnostics\fixture\capture-report.json'
    $prior=[ordered]@{
      schema_version=1;capture_kind='handshake_first_observed_remote_peers';stopped_at_utc=[DateTimeOffset]::UtcNow.AddMinutes(-1).ToString('o')
      plan=[ordered]@{
        expected_game_process_id=12345;duration_seconds=2;keeps_recording_same_fixed_peers_across_user_game_restart=$true
        scope_fingerprint=$options.ReviewedScopeFingerprint
        peer_filters=@(
          [pscustomobject]@{LocalAddress='192.0.2.10';RemoteAddress='192.0.2.1';RemotePort=10200},
          [pscustomobject]@{LocalAddress='192.0.2.10';RemoteAddress='192.0.2.2';RemotePort=12101},
          [pscustomobject]@{LocalAddress='192.0.2.10';RemoteAddress='192.0.2.3';RemotePort=21308}
        )
      }
    }
    if ($global:mode -eq 'prior_stale') {$prior.stopped_at_utc=[DateTimeOffset]::UtcNow.AddMinutes(-31).ToString('o')}
    if ($global:mode -eq 'prior_changed_peer') {$prior.plan.peer_filters[0].RemoteAddress='192.0.2.98'}
    if ($global:mode -eq 'prior_changed_local') {$prior.plan.peer_filters[0].LocalAddress='192.0.2.99'}
    $global:priorReportJson=$prior | ConvertTo-Json -Depth 6
}
try {
  $output=& ([scriptblock]::Create($source)) @options 3>&1 6>&1 | Out-String
} catch {$failure=$_.Exception.Message}
$adds=@($global:calls | Where-Object {$_ -like 'filter add *'})
$removes=@($global:calls | Where-Object {$_ -like 'filter remove*'})
$starts=@($global:calls | Where-Object {$_ -like 'start *'})
if ($global:mode -eq 'record_disabled') {
  if ($failure -notlike 'Live handshake capture is temporarily disabled*' -or $global:calls.Count -or $null -ne $global:report) {throw 'Live capture hold mutated settings or failed open.'}
} elseif ($global:mode -in @('preview','prior_preview')) {
  if ($failure -or $global:calls.Count -ne 0 -or $global:fixtureClock.Elapsed.TotalSeconds -ne 0) {throw 'Preview mutated or waited.'}
  $p=$output | ConvertFrom-Json
  if ($p.mode -ne 'preview_only' -or $p.peer_filters.Count -ne 3 -or $p.local_tcp_port_restricted -or
      -not $p.other_local_processes_using_same_peers_may_be_captured -or $p.new_remote_peers_added -or $p.changes_game_or_login -or
      $p.scope_fingerprint.Length -ne 64) {throw 'Preview misstates scope.'}
  if ($global:mode -eq 'prior_preview' -and ($p.initial_game_process_present -or $p.peer_source -ne 'recent_reviewed_report')) {throw 'Prior preview claims a current game.'}
} elseif ($global:mode -in @('prior_changed_peer','prior_stale','prior_game_present','prior_changed_local')) {
  if ($failure -notlike 'Prior*Nothing was captured.' -or $global:calls.Count) {throw 'Invalid prior scope mutated settings.'}
} elseif ($global:mode -in @('unreviewed','scope_changed')) {
  if ($failure -notlike 'Recording requires the unchanged preview scope fingerprint*' -or $global:calls.Count) {throw 'Unreviewed scope changed settings.'}
} elseif ($global:mode -eq 'existing') {
  if ($failure -notlike 'Expected an empty Packet Monitor filter list*' -or $adds.Count -or $removes.Count -or
      $global:calls -contains 'create-folder') {throw 'Existing filters changed.'}
} elseif ($global:mode -eq 'filter_failure') {
  if ($failure -notlike 'Could not add an exact game-peer filter*' -or $adds.Count -ne 2 -or $removes.Count -ne 1 -or $starts.Count) {throw 'Partial filter failure regression.'}
} elseif ($global:mode -eq 'start_failure') {
  if ($failure -ne 'Packet Monitor did not start.' -or $removes.Count -or $global:calls -contains 'stop' -or $null -ne $global:report) {throw 'Uncertain startup broadened or claimed capture.'}
} elseif ($global:mode -eq 'stop_failure') {
  if ($failure -or $removes.Count -or $null -ne $global:report -or $output -notmatch 'Automatic stop failed') {throw 'Failed stop removed filters or reported success.'}
} else {
  if ($failure) {throw $failure}
  $r=$global:report
  if ($null -eq $r -or $r.plan.peer_filters.Count -ne 3 -or $r.every_packet_process_ownership_verified -or
      $r.handshake_verified -or $r.career_access_verified -or $r.network_requests_sent_by_helper -or
      $r.game_changed -ne ($global:mode -in @('changed','restart','prior_record'))) {throw 'Incorrect report claims.'}
  if ($global:mode -eq 'prior_record' -and ($r.plan.initial_game_process_present -or $r.initial_game_connections.Count -ne 0 -or
      $r.game_process_ids_observed.Count -ne 1 -or $r.game_process_ids_observed[0] -ne 999 -or
      $r.prior_capture_report -ne $options.PriorCaptureReport)) {throw 'Historical metadata was claimed current.'}
  if ($global:mode -eq 'restart' -and ($r.game_process_ids_observed.Count -ne 2 -or
      $r.game_process_ids_observed -notcontains 999 -or $null -ne $r.observations[0].game_process_id)) {throw 'Restart metadata regression.'}
  if ($global:mode -eq 'count_failure') {
    if ($r.packet_count_verified -or $null -ne $r.packet_event_count) {throw 'Unreadable trace reported as zero.'}
  } elseif (-not $r.packet_count_verified -or $r.packet_event_count -ne 1) {throw 'Packet count regression.'}
  if ($adds.Count -ne 6 -or $starts.Count -ne 1) {throw 'Filter lifecycle regression.'}
  if ($global:mode -eq 'changed_filters') {
    if ($removes.Count -or -not $r.temporary_filter_cleanup_failed) {throw 'Changed filters blindly removed.'}
  } elseif ($removes.Count -ne 1 -or $removes[0] -ne 'filter remove' -or
      $global:calls.IndexOf($removes[0]) -le $global:calls.IndexOf('stop') -or $r.temporary_filter_cleanup_failed) {throw 'Filter cleanup order regression.'}
  if ($global:mode -eq 'changed') {
    if ($r.observations.Count -ne 0) {throw 'Followed changed process.'}
  } elseif ($r.outside_peer_count_observed -ne 2 -or $global:fixtureClock.Elapsed.TotalSeconds -ne 2) {throw 'Missing peer coverage/timer metadata.'}
}
foreach ($add in $adds) {
  if ($add -notmatch '^filter add MAKHello[0-9a-f]{8}[1-3](Out|In) -t TCP -i (192\.0\.2\.10 192\.0\.2\.[1-3]|192\.0\.2\.[1-3] 192\.0\.2\.10) -p (10200|12101|21308)$') {throw 'Unexpected or broader filter.'}
}
if (@($removes | Where-Object {$_ -ne 'filter remove'}).Count) {throw 'Unsupported named-filter deletion used.'}
Write-Output 'PASS: preview or bounded fixed-peer lifecycle verified with mocked commands only.'
""".replace("__SCRIPT__", str(SCRIPT).replace("'", "''")).replace("__MODE__", mode)
        for engine in ENGINES:
            with self.subTest(engine=engine):
                result = subprocess.run(
                    [engine, "-NoProfile", "-NonInteractive", "-Command", command],
                    capture_output=True, text=True, timeout=20,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("PASS:", result.stdout)
                self.assertNotIn("SYNTHETIC_SECRET_DO_NOT_OUTPUT", result.stdout)


if __name__ == "__main__":
    unittest.main()
