@echo off
setlocal
set "ITRTG_LAUNCHER=%~f0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$source = Get-Content -LiteralPath $env:ITRTG_LAUNCHER -Raw -Encoding UTF8; & ([scriptblock]::Create(($source -split '(?m)^# POWERSHELL\r?$', 2)[1]))"
set "ITRTG_EXIT_CODE=%ERRORLEVEL%"
if not "%ITRTG_EXIT_CODE%"=="0" pause
exit /b %ITRTG_EXIT_CODE%
# POWERSHELL
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$projectDirectory = Split-Path -Parent $env:ITRTG_LAUNCHER
$frontendDirectory = Join-Path $projectDirectory 'frontend'
$logDirectory = Join-Path $projectDirectory '.launcher'
$toolsDirectory = Join-Path $logDirectory 'tools'
$services = @()
$exitCode = 0

function Test-Ready($url) {
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 1
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Download-File($url, $destination) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $destination -UseBasicParsing -TimeoutSec 180
}

function Download-VerifiedFile($url, $destination, $sha256) {
    if ($sha256 -notmatch '^[a-fA-F0-9]{64}$') { throw 'The download checksum is invalid.' }
    Download-File $url $destination
    if ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -ne $sha256) {
        throw "Download verification failed for $(Split-Path -Leaf $destination). Run start.cmd again."
    }
}

function Test-Node($node, $npm) {
    if (-not (Test-Path -LiteralPath $node -PathType Leaf) -or -not (Test-Path -LiteralPath $npm -PathType Leaf)) { return $false }
    $previousPath = $env:PATH
    try {
        $version = & $node --version 2>$null
        if ($LASTEXITCODE -ne 0 -or $version -notmatch '^v(\d+)\.') { return $false }
        if ([int]$Matches[1] -lt 22) { return $false }
        $env:PATH = (Split-Path -Parent $node) + ';' + $previousPath
        $npmVersion = & $npm --version 2>$null
        return $LASTEXITCODE -eq 0 -and $npmVersion -match '^\d+\.'
    } catch {
        return $false
    } finally {
        $env:PATH = $previousPath
    }
}

function Initialize-Node {
    $systemNode = Get-Command node.exe -CommandType Application -ErrorAction SilentlyContinue
    $systemNpm = Get-Command npm.cmd -CommandType Application -ErrorAction SilentlyContinue
    if ($systemNode -and $systemNpm -and (Test-Node $systemNode.Source $systemNpm.Source)) {
        return [pscustomobject]@{ Node = $systemNode.Source; Npm = $systemNpm.Source }
    }
    New-Item -ItemType Directory -Path $toolsDirectory -Force | Out-Null
    foreach ($cached in (Get-ChildItem -LiteralPath $toolsDirectory -Directory -Filter 'node-v22.*-win-x64')) {
        $node = Join-Path $cached.FullName 'node.exe'
        $npm = Join-Path $cached.FullName 'npm.cmd'
        if (Test-Node $node $npm) { return [pscustomobject]@{ Node = $node; Npm = $npm } }
    }

    Write-Host 'Preparing local Node.js and npm...'
    $checksumFile = Join-Path $toolsDirectory 'node-SHASUMS256.txt'
    Download-File 'https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt' $checksumFile
    $checksums = Get-Content -LiteralPath $checksumFile -Raw
    if ($checksums -notmatch '(?m)^([a-fA-F0-9]{64})\s+\*?(node-(v22\.\d+\.\d+)-win-x64\.zip)\s*$') {
        throw 'Could not read the official Node.js download checksum.'
    }
    $sha256, $fileName, $version = $Matches[1], $Matches[2], $Matches[3]
    $archive = Join-Path $toolsDirectory $fileName
    Download-VerifiedFile "https://nodejs.org/dist/$version/$fileName" $archive $sha256
    Expand-Archive -LiteralPath $archive -DestinationPath $toolsDirectory -Force
    $directory = Join-Path $toolsDirectory ([IO.Path]::GetFileNameWithoutExtension($fileName))
    $node = Join-Path $directory 'node.exe'
    $npm = Join-Path $directory 'npm.cmd'
    if (-not (Test-Node $node $npm)) { throw 'Could not prepare local Node.js and npm. Run start.cmd again.' }
    return [pscustomobject]@{ Node = $node; Npm = $npm }
}

function Test-Uv($executable) {
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) { return $false }
    try {
        $version = & $executable --version 2>$null
        return $LASTEXITCODE -eq 0 -and $version -match '^uv (\d+\.\d+\.\d+)\b' -and [version]$Matches[1] -ge [version]'0.9.11'
    } catch {
        return $false
    }
}

function Initialize-Uv {
    $systemUv = Get-Command uv.exe -CommandType Application -ErrorAction SilentlyContinue
    if ($systemUv -and (Test-Uv $systemUv.Source)) { return $systemUv.Source }
    $directory = Join-Path $toolsDirectory 'uv-0.9.11'
    $executable = Join-Path $directory 'uv.exe'
    if (Test-Uv $executable) { return $executable }

    Write-Host 'Preparing local uv...'
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $fileName = 'uv-x86_64-pc-windows-msvc.zip'
    $release = 'https://github.com/astral-sh/uv/releases/download/0.9.11'
    $checksumFile = Join-Path $toolsDirectory ($fileName + '.sha256')
    Download-File "$release/$fileName.sha256" $checksumFile
    $checksums = Get-Content -LiteralPath $checksumFile -Raw
    if ($checksums -notmatch ('(?m)^([a-fA-F0-9]{64})\s+\*?' + [regex]::Escape($fileName) + '\s*$')) {
        throw 'Could not read the official uv download checksum.'
    }
    $archive = Join-Path $toolsDirectory $fileName
    Download-VerifiedFile "$release/$fileName" $archive $Matches[1]
    Expand-Archive -LiteralPath $archive -DestinationPath $directory -Force
    if (-not (Test-Uv $executable)) { throw 'Could not prepare local uv. Run start.cmd again.' }
    return $executable
}

function Test-TesseractEnglish($executable) {
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) { return $false }
    $check = New-Object System.Diagnostics.Process
    $check.StartInfo.FileName = $executable
    $check.StartInfo.Arguments = '--list-langs'
    $check.StartInfo.UseShellExecute = $false
    $check.StartInfo.CreateNoWindow = $true
    $check.StartInfo.RedirectStandardOutput = $true
    $check.StartInfo.RedirectStandardError = $true
    try {
        $check.Start() | Out-Null
        $languages = $check.StandardOutput.ReadToEndAsync()
        $diagnostic = $check.StandardError.ReadToEndAsync()
        if (-not $check.WaitForExit(10000)) {
            $check.Kill()
            return $false
        }
        $diagnostic.GetAwaiter().GetResult() | Out-Null
        return $check.ExitCode -eq 0 -and $languages.Result -match '(?m)^\s*eng\s*$'
    } catch {
        return $false
    } finally {
        $check.Dispose()
    }
}

function Find-Tesseract {
    if ($env:ITRTG_TESSERACT_PATH) {
        $configured = [Environment]::ExpandEnvironmentVariables($env:ITRTG_TESSERACT_PATH).Trim('"')
        if (-not (Test-TesseractEnglish $configured)) {
            throw 'ITRTG_TESSERACT_PATH must point to a working tesseract.exe with English (eng) language data. Correct or remove this override and run start.cmd again.'
        }
        return (Get-Item -LiteralPath $configured).FullName
    }
    $onPath = Get-Command tesseract.exe -CommandType Application -ErrorAction SilentlyContinue
    $candidates = @(
        $(if ($onPath) { $onPath.Source })
        $(if ($env:ProgramFiles) { Join-Path $env:ProgramFiles 'Tesseract-OCR\tesseract.exe' })
        $(if (${env:ProgramFiles(x86)}) { Join-Path ${env:ProgramFiles(x86)} 'Tesseract-OCR\tesseract.exe' })
        $(if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'Programs\Tesseract-OCR\tesseract.exe' })
        $(if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'Tesseract-OCR\tesseract.exe' })
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-TesseractEnglish $candidate)) {
            return $candidate
        }
    }
    return $null
}

function Initialize-Tesseract {
    $executable = Find-Tesseract
    if ($executable) { return $executable }
    if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is required to install Tesseract for the current user.' }

    Write-Host 'Preparing Tesseract with English data for the current user...'
    New-Item -ItemType Directory -Path $toolsDirectory -Force | Out-Null
    $installer = Join-Path $toolsDirectory 'tesseract-5.4.0.20240606-setup.exe'
    # Pinned UB Mannheim release; hash from the Microsoft WinGet package manifest.
    Download-VerifiedFile 'https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe' $installer 'C885FFF6998E0608BA4BB8AB51436E1C6775C2BAFC2559A19B423E18678B60C9'
    $installDirectory = Join-Path $env:LOCALAPPDATA 'Programs\Tesseract-OCR'
    # Install for this user without requesting administrator rights.
    $previousCompatibilityLayer = $env:__COMPAT_LAYER
    try {
        $env:__COMPAT_LAYER = 'RunAsInvoker'
        # NSIS requires /D to be last and its path to be unquoted, even with spaces.
        $installation = Start-Process -FilePath $installer -ArgumentList @('/S', '/CurrentUser', "/D=$installDirectory") -WindowStyle Hidden -Wait -PassThru
        $installerExitCode = $installation.ExitCode
    } finally {
        $env:__COMPAT_LAYER = $previousCompatibilityLayer
    }
    if ($installerExitCode -ne 0) {
        throw "Tesseract installation failed (exit code $installerExitCode). Run start.cmd again. If the error repeats, report this message."
    }
    $executable = Find-Tesseract
    if (-not $executable) {
        throw 'Tesseract installation did not provide a working executable with English (eng) data. Run start.cmd again. If the error repeats, report this message.'
    }
    return $executable
}

try {
    Set-Location -LiteralPath $projectDirectory
    if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { throw 'This launcher requires 64-bit (x64) Windows.' }
    # Check before downloading tools: Windows and Docker share the same UI address.
    $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    foreach ($port in @(8000, 8080)) {
        if ($listeners.Port -contains $port) { throw "Port $port is already in use. Stop the running optimizer (including Docker) and try again." }
    }
    $node = Initialize-Node
    $env:PATH = (Split-Path -Parent $node.Node) + ';' + $env:PATH
    $uv = Initialize-Uv

    # The pinned Windows OCR package provides Python 3.12 wheels.
    Write-Host 'Preparing Python dependencies...'
    & $uv sync --locked --python 3.12
    if ($LASTEXITCODE -ne 0) { throw 'Could not install Python dependencies.' }
    $python = Join-Path $projectDirectory '.venv\Scripts\python.exe'
    Write-Host 'Checking Tesseract OCR and English language data...'
    $env:ITRTG_TESSERACT_PATH = Initialize-Tesseract
    $lockHash = (Get-FileHash -LiteralPath (Join-Path $frontendDirectory 'package-lock.json') -Algorithm SHA256).Hash
    $lockStamp = Join-Path $logDirectory 'frontend-lock.sha256'
    $installedHash = if (Test-Path -LiteralPath $lockStamp) { (Get-Content -LiteralPath $lockStamp -Raw).Trim() } else { '' }
    if ($installedHash -ne $lockHash -or -not (Test-Path -LiteralPath (Join-Path $frontendDirectory 'node_modules\vite\bin\vite.js'))) {
        Write-Host 'Preparing frontend dependencies...'
        & $node.Npm --prefix $frontendDirectory ci
        if ($LASTEXITCODE -ne 0) { throw 'Could not install frontend dependencies.' }
        New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
        Set-Content -LiteralPath $lockStamp -Value $lockHash -Encoding ASCII
    }

    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    Write-Host 'Starting the optimizer...'
    $services += Start-Process -FilePath $python -ArgumentList @('-m', 'uvicorn', 'itrtg_web.main:app', '--app-dir', 'backend', '--host', '127.0.0.1', '--port', '8000') -WorkingDirectory $projectDirectory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDirectory 'backend.log') -RedirectStandardError (Join-Path $logDirectory 'backend.err.log')
    $services += Start-Process -FilePath $node.Node -ArgumentList @('node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '8080', '--strictPort') -WorkingDirectory $frontendDirectory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDirectory 'frontend.log') -RedirectStandardError (Join-Path $logDirectory 'frontend.err.log')

    $deadline = (Get-Date).AddSeconds(60)
    while ($true) {
        foreach ($service in $services) {
            $service.Refresh()
            if ($service.HasExited) { throw "A service stopped during startup. See the logs in $logDirectory" }
        }
        if ((Test-Ready 'http://127.0.0.1:8000/api/engines') -and (Test-Ready 'http://127.0.0.1:8080')) { break }
        if ((Get-Date) -ge $deadline) { throw "Startup timed out. See the logs in $logDirectory" }
        Start-Sleep -Milliseconds 500
    }

    Start-Process 'http://127.0.0.1:8080'
    Write-Host 'Optimizer ready: http://127.0.0.1:8080'
    Read-Host 'Keep this window open. Press Enter to stop the optimizer' | Out-Null
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    $exitCode = 1
} finally {
    # Let active searches save their best path before stopping the server.
    if ($services.Count -gt 0 -and -not $services[0].HasExited) {
        try {
            Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/shutdown' -Method Post -UseBasicParsing -TimeoutSec 15 | Out-Null
        } catch {
            Write-Host 'Could not wait for the search to stop. Check the last saved path in History on the next launch.'
        }
    }
    foreach ($service in $services) {
        $service.Refresh()
        if (-not $service.HasExited) {
            Start-Process -FilePath 'taskkill.exe' -ArgumentList @('/PID', $service.Id, '/T', '/F') -WindowStyle Hidden -Wait
        }
    }
}
exit $exitCode
