# Opt-in launch of the normal installed launcher with libcurl TLS diagnostics.
# DEFAULT IS METADATA ONLY. Obtain explicit user approval before -Launch.
# No capture, game-memory access, elevation, DLL loading or game-file changes.
# The child process tree can write TLS secrets locally until it exits. Do not
# upload/paste that file. This does not yet establish Career traffic decryption.
[CmdletBinding()]
param([switch]$Launch)

$ErrorActionPreference = 'Stop'

function Get-CareerTlsLaunchDecision {
    param(
        [bool]$GameExists, [bool]$LauncherExists, [bool]$SteamRunning,
        [bool]$GameRunning, [bool]$LauncherRunning, [bool]$ExistingTlsSetting
    )
    $reason = 'ready_for_explicit_opt_in'
    if (-not $GameExists -or -not $LauncherExists) { $reason = 'installation_unavailable' }
    elseif ($ExistingTlsSetting) { $reason = 'existing_tls_setting_left_untouched' }
    elseif ($GameRunning -or $LauncherRunning) { $reason = 'close_game_and_launcher_normally_first' }
    elseif (-not $SteamRunning) { $reason = 'start_steam_normally_first' }
    [pscustomobject]@{ can_launch = ($reason -eq 'ready_for_explicit_opt_in'); reason = $reason }
}

function New-CareerTlsStartInfo {
    param([string]$LauncherPath, [string]$WorkingDirectory, [string]$KeyLogPath)
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $LauncherPath
    $info.WorkingDirectory = $WorkingDirectory
    $info.UseShellExecute = $false
    # Only this new launcher's environment is changed. No process/user/machine
    # environment mutation and no Steam settings or persistent launch options.
    $info.EnvironmentVariables['SSLKEYLOGFILE'] = $KeyLogPath
    # This is the normal interactive game launcher, not a background helper.
    $info.WindowStyle = [Diagnostics.ProcessWindowStyle]::Normal
    return $info
}

function New-CareerTlsPrivateDirectory {
    param([string]$DirectoryPath)
    if (Test-Path -LiteralPath $DirectoryPath) { throw 'Diagnostic directory must be new.' }
    $null = New-Item -ItemType Directory -Path $DirectoryPath
    $owner = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $system = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetOwner($owner)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($principal in @($owner, $system)) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $principal, [Security.AccessControl.FileSystemRights]::FullControl,
            ([Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit),
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow)
        $acl.AddAccessRule($rule)
    }
    # Do not start any child or create a key file unless the private ACL succeeds.
    # Use .NET directly, without Security-module autoloading. Modern .NET
    # exposes ACLs as extension methods; Windows PowerShell's .NET Framework
    # exposes them on Directory. Both must protect the folder before launch.
    if ('System.IO.FileSystemAclExtensions' -as [type]) {
        [IO.FileSystemAclExtensions]::SetAccessControl([IO.DirectoryInfo]::new($DirectoryPath), $acl)
    } else {
        [IO.Directory]::SetAccessControl($DirectoryPath, $acl)
    }
}

$installDirectory = 'C:\Program Files (x86)\Steam\steamapps\common\MarvelRivals'
$launcherPath = Join-Path $installDirectory 'MarvelRivals_Launcher.exe'
$gamePath = Join-Path $installDirectory 'MarvelGame\Marvel\Binaries\Win64\Marvel-Win64-Shipping.exe'
$decision = Get-CareerTlsLaunchDecision `
    -GameExists (Test-Path -LiteralPath $gamePath -PathType Leaf) `
    -LauncherExists (Test-Path -LiteralPath $launcherPath -PathType Leaf) `
    -SteamRunning (@(Get-Process -Name 'steam' -ErrorAction SilentlyContinue).Count -gt 0) `
    -GameRunning (@(Get-Process -Name 'Marvel-Win64-Shipping','Marvel' -ErrorAction SilentlyContinue).Count -gt 0) `
    -LauncherRunning (@(Get-Process -Name 'MarvelRivals_Launcher' -ErrorAction SilentlyContinue).Count -gt 0) `
    -ExistingTlsSetting (-not [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable('SSLKEYLOGFILE', 'Process')))

if (-not $Launch) {
    [pscustomobject]@{
        outcome = 'metadata_only'; can_launch = $decision.can_launch; reason = $decision.reason
        launcher_started = $false; tls_setting_changed = $false; capture_started = $false
        keylog_file_created = $false; live_game_tls_logging_verified = $false
    } | ConvertTo-Json
    return
}
if (-not $decision.can_launch) { throw $decision.reason }

$expectedGameHash = '54154e879731f3f6fad32371bff78d62629c3d87ad5443dc9466871312a76714'
$expectedLauncherHash = 'cb56ae37b02f4cb888d61220aab847e7c340b235234752407251b0c6b26028f3'
if ((Get-FileHash -LiteralPath $gamePath -Algorithm SHA256).Hash -ne $expectedGameHash -or
    (Get-FileHash -LiteralPath $launcherPath -Algorithm SHA256).Hash -ne $expectedLauncherHash) {
    throw 'Installed build changed. Revalidate TLS support before this diagnostic.'
}
$localData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
if ([string]::IsNullOrWhiteSpace($localData)) { throw 'Local application-data directory unavailable.' }
$diagnosticDirectory = Join-Path $localData ('MarvelAccountKeeper\diagnostics\career-tls-' + [guid]::NewGuid().ToString('N'))
New-CareerTlsPrivateDirectory -DirectoryPath $diagnosticDirectory
$keyLogPath = Join-Path $diagnosticDirectory 'session.keys'
$startInfo = New-CareerTlsStartInfo -LauncherPath $launcherPath -WorkingDirectory $installDirectory -KeyLogPath $keyLogPath
$launcher = [Diagnostics.Process]::Start($startInfo)
if ($null -eq $launcher) { throw 'The normal launcher did not start. No capture was started.' }
[pscustomobject]@{
    outcome = 'diagnostic_launcher_started'; launcher_pid = $launcher.Id
    keylog_path = $keyLogPath; environment_scope = 'new_launcher_process_tree_only'
    persistent_setting_changed = $false; capture_started = $false
    live_game_tls_logging_verified = $false; career_access_verified = $false
} | ConvertTo-Json
Write-Host 'Use the normal launcher to reach the lobby. This first test only checks whether TLS key logging works.'
Write-Host 'Quit the diagnostic game and launcher normally when the test is finished; logging lasts for this launch.'
Write-Host 'Do not upload or paste session.keys. It can decrypt traffic from this diagnostic session.'
