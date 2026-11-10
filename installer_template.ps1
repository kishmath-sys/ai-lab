# AI Lab installer (ChatGPT + Claude + one Gemini review)
# Run it with:   powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\Downloads\Install-AILab.ps1"
# Safe to run again. It skips anything that is already installed and never touches your runs folder.

$ErrorActionPreference = 'Continue'
$AppDir = 'C:\ai-lab'
$Total = 8
$script:Results = @()

function Step($n, $text) {
    Write-Host ''
    Write-Host "[$n of $Total] $text" -ForegroundColor Cyan
}
function Ok($text)   { Write-Host "    OK  $text" -ForegroundColor Green }
function Note($text) { Write-Host "    ... $text" -ForegroundColor Gray }
function Bad($text)  { Write-Host "    !!  $text" -ForegroundColor Yellow }
function Result($name, $good, $detail) {
    $script:Results += [pscustomobject]@{ Name = $name; Good = $good; Detail = $detail }
}
function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user;$env:APPDATA\npm;$env:LOCALAPPDATA\Antigravity"
}
function Have($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Find-Python {
    # Returns the full path to a real Python 3 (not the Microsoft Store stub), or $null.
    $candidates = @()
    if (Have 'py') { $candidates += ,@('py', '-3') }
    if (Have 'python') { $candidates += ,@('python') }
    foreach ($c in $candidates) {
        try {
            $exe = $c[0]
            $rest = @()
            if ($c.Count -gt 1) { $rest = $c[1..($c.Count - 1)] }
            $out = & $exe @rest -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $out -and (Test-Path ([string]$out).Trim())) { return ([string]$out).Trim() }
        } catch { }
    }
    $found = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
    if ($found) { return $found.FullName }
    return $null
}

function Run-Quiet($file, $arguments, $stdinText) {
    # Runs a command, returns an object with ExitCode and Text. Never throws.
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($null -ne $stdinText) {
            $text = $stdinText | & $file @arguments 2>&1 | Out-String
        } else {
            $text = & $file @arguments 2>&1 | Out-String
        }
        $code = $LASTEXITCODE
    } catch {
        $text = $_.Exception.Message
        $code = 1
    }
    $ErrorActionPreference = $old
    return [pscustomobject]@{ ExitCode = $code; Text = $text }
}

Write-Host ''
Write-Host '  AI Lab installer' -ForegroundColor White
Write-Host '  ChatGPT researches. Claude checks and builds. Gemini reviews once. ChatGPT checks.' -ForegroundColor Gray
Write-Host '  This takes about 5 to 10 minutes. Windows may ask permission once or twice. Click Yes.' -ForegroundColor Gray

# ---------------------------------------------------------------- 1 winget
Step 1 'Checking Windows tools'
if (-not (Have 'winget')) {
    Bad 'winget was not found. Open the Microsoft Store, install "App Installer", then run this again.'
    exit 1
}
Ok 'winget is available'

# ---------------------------------------------------------------- 2 python
Step 2 'Python (runs the program)'
$py = Find-Python
if ($py) {
    Ok "already installed: $py"
} else {
    Note 'installing Python 3.12 ...'
    & winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Null
    Refresh-Path
    $py = Find-Python
    if ($py) { Ok "installed: $py" } else { Bad 'Python did not install. Close this window, open a new PowerShell, and run the installer again.'; exit 1 }
}
$pyDir = Split-Path $py
$pyw = Join-Path $pyDir 'pythonw.exe'
if (-not (Test-Path $pyw)) { $pyw = $py }

# ---------------------------------------------------------------- 3 node
Step 3 'Node.js (needed to install the AI tools)'
Refresh-Path
if ((Have 'node') -and (Have 'npm.cmd')) {
    Ok 'already installed'
} else {
    Note 'installing Node.js LTS ... a permission window may appear'
    & winget install -e --id OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements | Out-Null
    Refresh-Path
    if (-not (Have 'npm.cmd') -and (Test-Path "$env:ProgramFiles\nodejs\npm.cmd")) { $env:Path += ";$env:ProgramFiles\nodejs" }
    if (Have 'npm.cmd') { Ok 'installed' } else { Bad 'Node.js did not install. Close this window, open a new PowerShell, and run the installer again.'; exit 1 }
}
if (-not (Test-Path "$env:APPDATA\npm")) { New-Item -ItemType Directory -Path "$env:APPDATA\npm" -Force | Out-Null }
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath -notlike "*$env:APPDATA\npm*") {
    [Environment]::SetEnvironmentVariable('Path', "$userPath;$env:APPDATA\npm", 'User')
    Note 'added the npm folder to your PATH'
}
Refresh-Path

# ---------------------------------------------------------------- 4 AI tools
Step 4 'Claude Code, Codex and Gemini (the AI tools that use your subscriptions)'
$needClaude = -not (Have 'claude.cmd')
$needCodex = -not (Have 'codex.cmd')
if ($needClaude -or $needCodex) {
    $pkgs = @()
    if ($needClaude) { $pkgs += '@anthropic-ai/claude-code' }
    if ($needCodex) { $pkgs += '@openai/codex' }
    Note ('installing ' + ($pkgs -join ' and ') + ' ...')
    $r = Run-Quiet 'npm.cmd' (@('install', '-g') + $pkgs) $null
    if ($r.ExitCode -ne 0) {
        Note 'first try failed, trying once more ...'
        Start-Sleep -Seconds 3
        $r = Run-Quiet 'npm.cmd' (@('install', '-g') + $pkgs) $null
    }
    Refresh-Path
    if ($r.ExitCode -ne 0) { Bad ($r.Text.Substring(0, [Math]::Min(500, $r.Text.Length))) }
}
if (Have 'claude.cmd') { Ok 'Claude Code is installed' } else { Bad 'Claude Code is not installed'; Result 'Claude Code installed' $false 'run the installer again' }
if (Have 'codex.cmd') { Ok 'Codex is installed' } else { Bad 'Codex is not installed'; Result 'Codex installed' $false 'run the installer again' }

# Gemini (Antigravity CLI, command name agy). Optional: the program works without it.
$agyPath = Join-Path $env:LOCALAPPDATA 'Antigravity\agy.exe'
if ((Have 'agy') -or (Test-Path $agyPath)) {
    Ok 'Gemini (agy) is installed'
} else {
    Note 'installing Gemini (Antigravity CLI) ...'
    try {
        Invoke-RestMethod 'https://antigravity.google/cli/install.ps1' | Invoke-Expression
    } catch {
        Bad ('Gemini install problem: ' + $_.Exception.Message)
    }
    Refresh-Path
    if ((Have 'agy') -or (Test-Path $agyPath)) { Ok 'Gemini (agy) is installed' } else { Bad 'Gemini (agy) did not install. AI Lab still works, it will just skip the Gemini review.'; Result 'Gemini installed (optional)' $false 'AI Lab works without it' }
}

# ---------------------------------------------------------------- 5 python packages
Step 5 'Word, PDF and reading tools for Python'
$r = Run-Quiet $py @('-m', 'pip', 'install', '--user', '--quiet', '--disable-pip-version-check', 'python-docx', 'pypdf', 'markdown') $null
if ($r.ExitCode -eq 0) { Ok 'python-docx, pypdf, markdown' } else { Bad ($r.Text.Substring(0, [Math]::Min(400, $r.Text.Length))); Result 'Python packages' $false 'pip failed' }

# ---------------------------------------------------------------- 6 app files
Step 6 'Installing AI Lab into C:\ai-lab'
$payload = @'
__PAYLOAD__
'@
try {
    New-Item -ItemType Directory -Path $AppDir -Force | Out-Null
    New-Item -ItemType Directory -Path "$AppDir\runs" -Force | Out-Null
    $zip = Join-Path $env:TEMP 'ailab-app.zip'
    [IO.File]::WriteAllBytes($zip, [Convert]::FromBase64String(($payload -replace '\s', '')))
    Expand-Archive -Path $zip -DestinationPath $AppDir -Force
    Remove-Item $zip -Force
    Ok "program files are in $AppDir"
} catch {
    Bad ('Could not write the program files: ' + $_.Exception.Message)
    exit 1
}

# desktop shortcut (works with OneDrive desktops too)
try {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut((Join-Path $desktop 'AI Lab.lnk'))
    $link.TargetPath = $pyw
    $link.Arguments = '"' + $AppDir + '\launcher.pyw"'
    $link.WorkingDirectory = $AppDir
    $link.Description = 'AI Lab: ChatGPT and Claude working together'
    $link.Save()
    Ok "desktop icon 'AI Lab' created"
} catch {
    Bad ('Could not make the desktop icon: ' + $_.Exception.Message + '  You can still start it by double-clicking C:\ai-lab\launcher.pyw')
}

# ---------------------------------------------------------------- 7 sign in
Step 7 'Signing in (uses your own subscriptions, no API keys)'
foreach ($name in 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'OPENAI_API_KEY', 'CODEX_API_KEY') {
    if (Test-Path "Env:$name") { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
}
Set-Location $AppDir

$script:ClaudeSaid = ''
function Test-Claude {
    $r = Run-Quiet 'claude.cmd' @('-p', 'Reply with the single word OK.') $null
    $script:ClaudeSaid = $r.Text.Trim()
    return ($r.ExitCode -eq 0 -and $r.Text -match 'OK')
}
function Diagnose-Claude {
    Write-Host ''
    Write-Host '    Claude said:' -ForegroundColor Yellow
    $said = $script:ClaudeSaid
    if ($said.Length -gt 400) { $said = $said.Substring(0, 400) }
    Write-Host ('    ' + ($said -replace "`r?`n", "`n    ")) -ForegroundColor Gray
    Write-Host '    Checking your internet connection to Anthropic (200, 301, 302 and 404 are fine; 403 or 000 means blocked):' -ForegroundColor Yellow
    foreach ($u in 'https://claude.ai', 'https://api.anthropic.com', 'https://downloads.claude.ai/claude-code-releases/latest') {
        $code = ''
        try { $code = (& curl.exe -s -o NUL -w '%{http_code}' -m 15 $u 2>$null) } catch { $code = 'error' }
        Write-Host ("      $u  ->  $code") -ForegroundColor Gray
    }
    Write-Host '    If you see 403 or 000: you may be on a school or work network, or using a VPN. Turn off the VPN, or try another network such as your phone hotspot, then run this installer again.' -ForegroundColor Yellow
    Write-Host '    If the numbers look fine: close this window, open a NEW PowerShell, type  claude.cmd  and sign in.' -ForegroundColor Yellow
}
function Test-Codex {
    $r = Run-Quiet 'codex.cmd' @('exec', '--skip-git-repo-check', '--sandbox', 'read-only', '-') 'Reply with the single word OK.'
    return ($r.ExitCode -eq 0 -and $r.Text -match 'OK')
}

$codexOk = $false
$claudeOk = $false
if (Have 'codex.cmd') {
    Note 'checking ChatGPT (Codex) ...'
    $codexOk = Test-Codex
    if (-not $codexOk) {
        Write-Host ''
        Write-Host '    A browser window will open. Sign in with your ChatGPT account.' -ForegroundColor White
        & codex.cmd login
        $codexOk = Test-Codex
    }
    if ($codexOk) { Ok 'ChatGPT is signed in' } else { Bad 'ChatGPT sign-in did not work yet. You can fix it later: open PowerShell and type  codex login' }
}
if (Have 'claude.cmd') {
    Note 'checking Claude ...'
    $claudeOk = Test-Claude
    if (-not $claudeOk) {
        Write-Host ''
        Write-Host '    Claude will open now. Pick a look, then choose to sign in with your Claude account (browser).' -ForegroundColor White
        Write-Host '    When you reach the prompt, type  /exit  and press Enter to come back here.' -ForegroundColor White
        & claude.cmd
        $claudeOk = Test-Claude
    }
    if ($claudeOk) { Ok 'Claude is signed in' } else { Bad 'Claude sign-in did not work yet.'; Diagnose-Claude }
}

$geminiOk = $false
if ((Have 'agy') -or (Test-Path $agyPath)) {
    Note 'checking Gemini ...'
    New-Item -ItemType Directory -Path "$AppDir\_scratch" -Force | Out-Null
    function Test-Gemini {
        $r = Run-Quiet $py @("$AppDir\ailab.py", '--ping', 'gemini') $null
        return ($r.Text -match '(?m)^OK')
    }
    $geminiOk = Test-Gemini
    if (-not $geminiOk) {
        Write-Host ''
        Write-Host '    Gemini will open now. Sign in with your Google account in the browser (paste the code back here if asked).' -ForegroundColor White
        Write-Host '    Accept the terms, say yes to trusting the folder, pick any color scheme.' -ForegroundColor White
        Write-Host '    When you reach the prompt, type  /exit  and press Enter to come back here.' -ForegroundColor White
        Push-Location "$AppDir\_scratch"
        if (Test-Path $agyPath) { & $agyPath } else { & agy }
        Pop-Location
        $geminiOk = Test-Gemini
    }
    if ($geminiOk) { Ok 'Gemini is signed in' } else { Bad 'Gemini sign-in did not work yet. Optional. To fix: open PowerShell, type  cd C:\ai-lab\_scratch  then  agy' }
}

# ---------------------------------------------------------------- 8 self test
Step 8 'Testing everything'
$r = Run-Quiet $py @("$AppDir\convert.py") $null
$good = ($r.Text -match 'docx ok')
Result 'Reads and writes Word files' $good ($r.Text.Trim())

$testRun = Join-Path $env:TEMP ('ailab-test-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $testRun -Force | Out-Null
$utf8 = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText("$testRun\brief.md", 'Practice run.', $utf8)
[IO.File]::WriteAllText("$testRun\run.json", '{"format":"doc_web","title":"Install test","steps":[["gemini","research",""],["chatgpt","crosscheck",""],["claude","build",""],["chatgpt","review_full",""],["claude","fix",""]]}', $utf8)
$r = Run-Quiet $py @("$AppDir\ailab.py", $testRun, '--mock') $null
$made = (Test-Path "$testRun\files\install-test.docx") -and (Test-Path "$testRun\files\install-test-interactive.html") -and (Test-Path "$testRun\accessibility-report.md")
$practiceDetail = ''
if (-not ($r.ExitCode -eq 0 -and $made)) { $practiceDetail = ($r.Text.Trim()); if ($practiceDetail.Length -gt 300) { $practiceDetail = $practiceDetail.Substring($practiceDetail.Length - 300) } }
Result 'Practice run makes Word, web page and accessibility report' ($r.ExitCode -eq 0 -and $made) $practiceDetail
Remove-Item $testRun -Recurse -Force -ErrorAction SilentlyContinue

Result 'Claude signed in' $claudeOk 'open a NEW PowerShell, type  claude.cmd  and sign in'
Result 'ChatGPT signed in' $codexOk 'open a NEW PowerShell, type  codex.cmd login'
if ((Have 'agy') -or (Test-Path $agyPath)) { Result 'Gemini signed in (optional)' $geminiOk 'Gemini review will be skipped until fixed' }

Write-Host ''
Write-Host '  Results' -ForegroundColor White
$allGood = $true
foreach ($item in $script:Results) {
    if ($item.Good) { Write-Host ('   [ OK ] ' + $item.Name) -ForegroundColor Green }
    else {
        Write-Host ('   [FIX ] ' + $item.Name + '  ' + $item.Detail) -ForegroundColor Yellow
        if ($item.Name -notlike '*optional*') { $allGood = $false }
    }
}
Write-Host ''
if ($allGood) {
    Write-Host '  All set. Use the AI Lab icon on your desktop.' -ForegroundColor Green
} else {
    Write-Host '  Almost there. Fix the lines marked FIX, or run this installer again.' -ForegroundColor Yellow
}
Write-Host '  Important: this PowerShell window does not know the new commands yet. Close it and open a new one before typing claude.cmd or codex.cmd.' -ForegroundColor White
$answer = Read-Host '  Open AI Lab now? (Y/n)'
if ($answer -notmatch '^[nN]') { Start-Process -FilePath $pyw -ArgumentList ('"' + $AppDir + '\launcher.pyw"') -WorkingDirectory $AppDir }
