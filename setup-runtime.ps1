$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
Add-Type -AssemblyName System.IO.Compression.FileSystem
$runtimePath = Join-Path $PSScriptRoot '.runtime'
New-Item -ItemType Directory -Force -Path $runtimePath | Out-Null

function Get-VerifiedArchive($Uri, $Destination, $Hash) {
    if (-not (Test-Path -LiteralPath $Destination)) {
        Write-Host 'Downloading local service runtime...'
        Invoke-WebRequest -Uri $Uri -OutFile $Destination -UseBasicParsing
    }
    if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -ne $Hash) {
        throw "Archive verification failed: $Destination. Remove this archive and retry."
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $runtimePath 'pgsql/bin/pg_ctl.exe'))) {
    $archivePath = Join-Path $runtimePath 'postgresql-17.11.zip'
    Get-VerifiedArchive 'https://sbp.enterprisedb.com/getfile.jsp?fileid=1260616' $archivePath '80379B2C04D51C30225532E0AE04509899141E9957ED096FE749D7FD9DF8F82F'
    $archive = [IO.Compression.ZipFile]::OpenRead($archivePath)
    try {
        foreach ($entry in $archive.Entries) {
            if ($entry.FullName -match '^pgsql/(bin|lib|share)/' -and $entry.Name) {
                $target = [IO.Path]::GetFullPath((Join-Path $runtimePath $entry.FullName))
                $prefix = [IO.Path]::GetFullPath($runtimePath) + [IO.Path]::DirectorySeparatorChar
                if (-not $target.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid archive path.' }
                New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($target)) | Out-Null
                [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)
            }
        }
    } finally { $archive.Dispose() }
}
if (-not (Test-Path -LiteralPath (Join-Path $runtimePath 'mailpit/mailpit.exe'))) {
    $archivePath = Join-Path $runtimePath 'mailpit-1.33.0.zip'
    Get-VerifiedArchive 'https://github.com/axllent/mailpit/releases/download/v1.33.0/mailpit-windows-amd64.zip' $archivePath '47122DD828FF80DCAEF62B2952C478B178AFADADF066C990489608E537121BFD'
    $mailpitPath = Join-Path $runtimePath 'mailpit'
    New-Item -ItemType Directory -Force -Path $mailpitPath | Out-Null
    $archive = [IO.Compression.ZipFile]::OpenRead($archivePath)
    try {
        $entry = $archive.GetEntry('mailpit.exe')
        if (-not $entry) { throw 'Mailpit executable missing in archive.' }
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $mailpitPath 'mailpit.exe'), $true)
    } finally { $archive.Dispose() }
}
Write-Host 'Local PostgreSQL and Mailpit are ready.'
