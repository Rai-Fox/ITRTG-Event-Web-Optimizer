"""The Windows launcher prepares private tools without changing installed versions."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="The launcher uses Windows PowerShell.")
def test_launcher_bootstraps_tools_reuses_them_and_rejects_unverified_downloads(tmp_path):
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell 5.1 is unavailable.")
    launcher = Path(__file__).resolve().parents[1] / "start.cmd"
    harness = tmp_path / "check-launcher.ps1"
    harness.write_text(BOOTSTRAP_CHECKS, encoding="utf-8-sig")

    result = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(harness), str(launcher), str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr


BOOTSTRAP_CHECKS = r'''
param($launcher, $scratch)
$ErrorActionPreference = 'Stop'
Import-Module Microsoft.PowerShell.Utility
$source = ((Get-Content -LiteralPath $launcher -Raw -Encoding UTF8) -split '(?m)^# POWERSHELL\r?$', 2)[1]
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors.Message -join [Environment]::NewLine) }
foreach ($definition in $ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst]}, $false)) {
    Invoke-Expression $definition.Extent.Text
}

# Tiny native commands make version checks and paths with spaces real, not mocked.
$fakeTool = Join-Path $scratch 'tool.exe'
Add-Type -OutputAssembly $fakeTool -OutputType ConsoleApplication -TypeDefinition @"
using System;
using System.Diagnostics;
using System.IO;
public class LauncherTool {
    public static void Main(string[] args) {
        string executable = Process.GetCurrentProcess().MainModule.FileName;
        Console.WriteLine(Path.GetFileName(executable) == "uv.exe" ?
            executable.Contains("old uv") ? "uv 0.2.0" : "uv 0.9.11" :
            executable.Contains("old node") ? "v20.1.0" : "v22.20.0");
    }
}
"@
$nodeName = 'node-v22.20.0-win-x64'
$nodeSource = Join-Path $scratch $nodeName
$uvSource = Join-Path $scratch 'uv-source'
New-Item -ItemType Directory -Path $nodeSource, $uvSource | Out-Null
Copy-Item -LiteralPath $fakeTool -Destination (Join-Path $nodeSource 'node.exe')
Copy-Item -LiteralPath $fakeTool -Destination (Join-Path $uvSource 'uv.exe')
Set-Content -LiteralPath (Join-Path $nodeSource 'npm.cmd') -Encoding ASCII -Value "@node.exe --version >nul`r`n@echo 10.9.3"
$nodeZip = Join-Path $scratch 'node.zip'
$uvZip = Join-Path $scratch 'uv.zip'
Compress-Archive -LiteralPath $nodeSource -DestinationPath $nodeZip
Compress-Archive -Path (Join-Path $uvSource '*') -DestinationPath $uvZip
$nodeHash = (Get-FileHash -LiteralPath $nodeZip -Algorithm SHA256).Hash
$uvHash = (Get-FileHash -LiteralPath $uvZip -Algorithm SHA256).Hash
$requests = New-Object 'System.Collections.Generic.List[string]'
$systemCommands = @{}
function Get-Command {
    param($Name, $CommandType, $ErrorAction)
    return $systemCommands[$Name]
}
function Invoke-WebRequest {
    param($Uri, $OutFile, [switch]$UseBasicParsing, $TimeoutSec)
    $requests.Add([string]$Uri)
    switch ([string]$Uri) {
        'https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt' {
            Set-Content -LiteralPath $OutFile -Encoding ASCII -Value "$nodeHash  $nodeName.zip"
        }
        'https://nodejs.org/dist/v22.20.0/node-v22.20.0-win-x64.zip' {
            Copy-Item -LiteralPath $nodeZip -Destination $OutFile
            if ($corruptDownload) { Add-Content -LiteralPath $OutFile -Value 'corruption' }
        }
        'https://github.com/astral-sh/uv/releases/download/0.9.11/uv-x86_64-pc-windows-msvc.zip.sha256' {
            Set-Content -LiteralPath $OutFile -Encoding ASCII -Value "$uvHash  uv-x86_64-pc-windows-msvc.zip"
        }
        'https://github.com/astral-sh/uv/releases/download/0.9.11/uv-x86_64-pc-windows-msvc.zip' {
            Copy-Item -LiteralPath $uvZip -Destination $OutFile
        }
        default { throw "Unexpected download: $Uri" }
    }
}

# A fresh machine gets verified archives and can use both tools in this process.
$env:PATH = Join-Path $env:SystemRoot 'System32'
$originalPath = $env:PATH
$toolsDirectory = Join-Path $scratch 'Optimizer With Spaces\.launcher\tools'
$node = Initialize-Node
$uv = Initialize-Uv
if (-not $node.Node.StartsWith($toolsDirectory) -or -not $uv.StartsWith($toolsDirectory)) {
    throw 'Tools were not prepared in the private project directory.'
}
if ($requests.Count -ne 4) { throw 'The fresh setup did not use the official archives and checksums.' }
if ($env:PATH -ne $originalPath) { throw 'Preparation changed the caller PATH.' }
$env:PATH = (Split-Path -Parent $node.Node) + ';' + $env:PATH
& $node.Npm --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'npm is not usable in the current process.' }
& $uv --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'uv is not usable without refreshing PATH.' }

# Later launches reuse private tools without network access.
if ((Initialize-Node).Node -ne $node.Node -or (Initialize-Uv) -ne $uv -or $requests.Count -ne 4) {
    throw 'The cached tools were downloaded again.'
}

# Working system tools win; an old Node or missing npm uses the private copy.
$systemCommands['node.exe'] = [pscustomobject]@{Source=(Join-Path $nodeSource 'node.exe')}
$systemCommands['npm.cmd'] = [pscustomobject]@{Source=(Join-Path $nodeSource 'npm.cmd')}
$systemCommands['uv.exe'] = [pscustomobject]@{Source=(Join-Path $uvSource 'uv.exe')}
if ((Initialize-Node).Node -ne $systemCommands['node.exe'].Source -or (Initialize-Uv) -ne $systemCommands['uv.exe'].Source) {
    throw 'Working installed tools were not reused.'
}
$oldDirectory = Join-Path $scratch 'old node'
New-Item -ItemType Directory -Path $oldDirectory | Out-Null
Copy-Item -LiteralPath $fakeTool -Destination (Join-Path $oldDirectory 'node.exe')
$systemCommands['node.exe'] = [pscustomobject]@{Source=(Join-Path $oldDirectory 'node.exe')}
if ((Initialize-Node).Node -ne $node.Node) { throw 'An old system Node was accepted.' }
$systemCommands['node.exe'] = [pscustomobject]@{Source=(Join-Path $nodeSource 'node.exe')}
$systemCommands.Remove('npm.cmd')
if ((Initialize-Node).Node -ne $node.Node -or $requests.Count -ne 4) {
    throw 'Missing npm did not reuse the private Node and npm.'
}
$oldUvDirectory = Join-Path $scratch 'old uv'
New-Item -ItemType Directory -Path $oldUvDirectory | Out-Null
Copy-Item -LiteralPath $fakeTool -Destination (Join-Path $oldUvDirectory 'uv.exe')
$systemCommands['uv.exe'] = [pscustomobject]@{Source=(Join-Path $oldUvDirectory 'uv.exe')}
if ((Initialize-Uv) -ne $uv -or $requests.Count -ne 4) {
    throw 'An incompatible system uv was accepted instead of reusing the private copy.'
}

# A corrupt download must never be extracted or executed.
$systemCommands.Clear()
$toolsDirectory = Join-Path $scratch 'Corrupt Download'
$corruptDownload = $true
$rejected = $false
try { Initialize-Node | Out-Null } catch { $rejected = $_.Exception.Message -match 'Download verification failed' }
if (-not $rejected -or (Test-Path -LiteralPath (Join-Path $toolsDirectory $nodeName))) {
    throw 'An archive with the wrong checksum was extracted.'
}

# Tesseract uses a verified per-user installer, returns only its path, and restores compatibility settings.
$readyTesseract = $null
function Find-Tesseract { return $readyTesseract }
function Download-VerifiedFile {
    param($url, $destination, $sha256)
    if ($url -ne 'https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe' -or
        $sha256 -ne 'C885FFF6998E0608BA4BB8AB51436E1C6775C2BAFC2559A19B423E18678B60C9') {
        throw 'The Tesseract installer was not pinned to its official source and checksum.'
    }
}
function Start-Process {
    param($FilePath, $ArgumentList, $WindowStyle, [switch]$Wait, [switch]$PassThru)
    if ($WindowStyle -ne 'Hidden' -or $env:__COMPAT_LAYER -ne 'RunAsInvoker' -or
        ($ArgumentList -join ' ') -notmatch '^/S /CurrentUser /D=.+Tesseract-OCR$') {
        throw 'Tesseract did not use hidden per-user installation.'
    }
    if ($installationFails) { return [pscustomobject]@{ExitCode=21} }
    $script:readyTesseract = Join-Path $env:LOCALAPPDATA 'Programs\Tesseract-OCR\tesseract.exe'
    return [pscustomobject]@{ExitCode=0}
}
$env:LOCALAPPDATA = Join-Path $scratch 'User With Spaces'
$env:__COMPAT_LAYER = 'OriginalCompatibility'
$prepared = @(Initialize-Tesseract)
if ($prepared.Count -ne 1 -or $prepared[0] -ne $readyTesseract -or $env:__COMPAT_LAYER -ne 'OriginalCompatibility') {
    throw 'Tesseract preparation returned installer output or changed compatibility settings.'
}
$readyTesseract = $null
$installationFails = $true
$rejected = $false
try { Initialize-Tesseract | Out-Null } catch { $rejected = $_.Exception.Message -match 'exit code 21' }
if (-not $rejected -or $env:__COMPAT_LAYER -ne 'OriginalCompatibility') {
    throw 'An installer failure was hidden or compatibility settings leaked.'
}
'''
