"""Test the real PowerShell planning functions without launching or capturing."""

from pathlib import Path
import shutil
import subprocess
import unittest


POWERSHELL_ENGINES = tuple(path for name in ("powershell", "pwsh") if (path := shutil.which(name)))


@unittest.skipUnless(POWERSHELL_ENGINES, "PowerShell required")
class CareerTlsLaunchTest(unittest.TestCase):
    def assert_passes_in_available_engines(self, command):
        for engine in POWERSHELL_ENGINES:
            with self.subTest(engine=engine):
                result = subprocess.run(
                    [engine, "-NoProfile", "-NonInteractive", "-Command", command],
                    capture_output=True, text=True, timeout=20,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("PASS:", result.stdout)

    def test_syntax_decisions_and_child_environment(self):
        script = Path(__file__).parents[1] / "scripts" / "launch-career-tls.ps1"
        # Parse only, then execute two pure function bodies. The script's launch,
        # process discovery, hash reads and filesystem/ACL actions never run.
        command = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile('__SCRIPT__', [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'PowerShell syntax error.' }
$decisionAst = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Get-CareerTlsLaunchDecision'}, $true)
$startAst = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'New-CareerTlsStartInfo'}, $true)
$decide = $decisionAst.Body.GetScriptBlock()
$ready = @{ GameExists=$true; LauncherExists=$true; SteamRunning=$true; GameRunning=$false; LauncherRunning=$false; ExistingTlsSetting=$false }
$result = & $decide @ready
if (-not $result.can_launch) { throw 'Valid fixture blocked.' }
$cases = @(
    @{Field='GameExists'; Value=$false; Reason='installation_unavailable'},
    @{Field='LauncherExists'; Value=$false; Reason='installation_unavailable'},
    @{Field='SteamRunning'; Value=$false; Reason='start_steam_normally_first'},
    @{Field='GameRunning'; Value=$true; Reason='close_game_and_launcher_normally_first'},
    @{Field='LauncherRunning'; Value=$true; Reason='close_game_and_launcher_normally_first'},
    @{Field='ExistingTlsSetting'; Value=$true; Reason='existing_tls_setting_left_untouched'}
)
foreach ($case in $cases) {
    $fixture = $ready.Clone(); $fixture[$case.Field] = $case.Value
    $result = & $decide @fixture
    if ($result.can_launch -or $result.reason -ne $case.Reason) { throw 'Decision regression.' }
}
$original = [Environment]::GetEnvironmentVariable('SSLKEYLOGFILE', 'Process')
$info = & $startAst.Body.GetScriptBlock() -LauncherPath 'C:\Synthetic\launcher.exe' -WorkingDirectory 'C:\Synthetic' -KeyLogPath 'C:\Synthetic Private\session.keys'
if ($info.UseShellExecute -or $info.FileName -ne 'C:\Synthetic\launcher.exe' -or $info.WorkingDirectory -ne 'C:\Synthetic') { throw 'Launch configuration regression.' }
if ($info.EnvironmentVariables['SSLKEYLOGFILE'] -ne 'C:\Synthetic Private\session.keys') { throw 'Missing child-only setting.' }
if ([Environment]::GetEnvironmentVariable('SSLKEYLOGFILE', 'Process') -cne $original) { throw 'Parent environment changed.' }
if ($info.Arguments -or $info.Verb) { throw 'Unexpected launch arguments or elevation.' }
Write-Output 'PASS: syntax, seven decisions, child-only environment; no launch or file changes.'
""".replace("__SCRIPT__", str(script).replace("'", "''"))
        self.assert_passes_in_available_engines(command)

    def test_private_directory_acl_with_empty_synthetic_fixture(self):
        script = Path(__file__).parents[1] / "scripts" / "launch-career-tls.ps1"
        # Exercise only directory creation/ACLs on a new, empty temp directory.
        # Never launch the game, create secrets, or modify an existing directory.
        command = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile('__SCRIPT__', [ref]$tokens, [ref]$errors)
$function = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'New-CareerTlsPrivateDirectory'}, $true)
$temporaryParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$fixtureDirectory = Join-Path $temporaryParent ('mak-tls-acl-test-' + [guid]::NewGuid().ToString('N'))
if (Test-Path -LiteralPath $fixtureDirectory) { throw 'Fixture already exists.' }
try {
    & $function.Body.GetScriptBlock() -DirectoryPath $fixtureDirectory
    if ('System.IO.FileSystemAclExtensions' -as [type]) {
        $acl = [IO.FileSystemAclExtensions]::GetAccessControl([IO.DirectoryInfo]::new($fixtureDirectory))
    } else {
        $acl = [IO.Directory]::GetAccessControl($fixtureDirectory)
    }
    if (-not $acl.AreAccessRulesProtected) { throw 'Directory inherits broader access.' }
    $owner = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    if ($rules.Count -ne 2) { throw 'Unexpected ACL rule count.' }
    foreach ($rule in $rules) {
        if ($rule.IdentityReference.Value -notin @($owner, 'S-1-5-18') -or $rule.IsInherited) { throw 'Unexpected principal or inherited rule.' }
        if ($rule.AccessControlType -ne 'Allow' -or $rule.FileSystemRights -ne 'FullControl') { throw 'Unexpected permissions.' }
    }
    $rejected = $false
    try { & $function.Body.GetScriptBlock() -DirectoryPath $fixtureDirectory } catch { $rejected = $true }
    if (-not $rejected) { throw 'Existing directory was accepted.' }
    Write-Output 'PASS: private ACL and existing-directory rejection; no secrets or game launch.'
}
finally {
    # Exact generated target, still directly under the intended temporary root;
    # no recursive removal and no removal if unexpected contents appeared.
    if ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($fixtureDirectory)).TrimEnd('\') -ne $temporaryParent.TrimEnd('\')) { throw 'Unexpected cleanup path.' }
    if ((Test-Path -LiteralPath $fixtureDirectory) -and @(Get-ChildItem -LiteralPath $fixtureDirectory -Force).Count -eq 0) {
        Remove-Item -LiteralPath $fixtureDirectory
    }
}
""".replace("__SCRIPT__", str(script).replace("'", "''"))
        self.assert_passes_in_available_engines(command)


if __name__ == "__main__":
    unittest.main()
