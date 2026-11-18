param([switch]$CheckOnly, [switch]$Test)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

# Use the project's portable Python, or a regular Python installation.
$pythonExe = Join-Path $PSScriptRoot '.runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    $pythonExe = $null
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $candidate = & py -3 -c 'import sys; print(sys.executable)'
        if ($LASTEXITCODE -eq 0 -and $candidate) { $pythonExe = $candidate.Trim() }
    }
    if (-not $pythonExe) { throw 'Install Python 3.12 or newer, then run start.cmd again.' }
}

$env:PYTHONUTF8 = '1'
$packagePath = Join-Path $PSScriptRoot '.packages'
$env:PYTHONPATH = $packagePath
$tempPath = Join-Path $PSScriptRoot 'data\tmp'
New-Item -ItemType Directory -Force -Path $tempPath | Out-Null
$env:TEMP = $tempPath
$env:TMP = $tempPath
$savedErrorPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $pythonExe -c "import django, psycopg; assert django.get_version() == '5.2.19'" 2>$null
$probeExit = $LASTEXITCODE
$ErrorActionPreference = $savedErrorPreference
if ($probeExit -ne 0) {
    & $pythonExe -m pip install --no-cache-dir --upgrade --target $packagePath -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
if (-not (Test-Path -LiteralPath '.env') -or (Select-String -LiteralPath '.env' -Pattern '^LOCAL_SERVICES=1$' -Quiet)) {
    & (Join-Path $PSScriptRoot 'setup-runtime.ps1')
    & $pythonExe local_services.py
    if ($LASTEXITCODE -ne 0) { throw 'Local PostgreSQL/SMTP startup failed.' }
}
if ($Test) {
    & $pythonExe manage.py test --verbosity 1
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
    exit 0
}
& $pythonExe manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { throw 'Database setup failed.' }
& $pythonExe manage.py seed_demo
if ($LASTEXITCODE -ne 0) { throw 'Demo setup failed.' }
& $pythonExe manage.py check
if ($LASTEXITCODE -ne 0) { throw 'Project check failed.' }
Write-Host 'ShelfS: http://127.0.0.1:8000'
Write-Host 'Local demo accounts: .demo-credentials.txt'
Write-Host 'Local mailbox: http://127.0.0.1:8025'
if (-not $CheckOnly) { & $pythonExe run_local.py }
