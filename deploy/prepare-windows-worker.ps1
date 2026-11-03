# Dedicated, vendor-verified embeddable runtime. Author: FubukiHanasaki.
param([string]$Destination = 'tmp\windows-worker-runtime')
$ErrorActionPreference = 'Stop'
$zip = 'tmp\python-3.13.16-embed-amd64.zip'
$hash = '97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297'
if (Test-Path $Destination) { throw 'Destination must not exist; never reuse the Agent environment.' }
New-Item -ItemType Directory -Force tmp | Out-Null
Invoke-WebRequest 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-embed-amd64.zip' -OutFile $zip
if ((Get-FileHash $zip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $hash) { throw 'Python package checksum mismatch' }
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value

function Set-RuntimeOwnerAccess([string]$Path, [bool]$Directory) {
    # Grant the owner first, then remove inheritance. Directory inheritance
    # flags must not be used as the file's explicit access rule.
    $permission = if ($Directory) { "*${sid}:(OI)(CI)F" } else { "*${sid}:F" }
    icacls $Path /grant:r $permission /Q | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot grant runtime owner access' }
    icacls $Path /inheritance:r /Q | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot secure runtime ACL' }
}

New-Item -ItemType Directory -Path $Destination | Out-Null
Set-RuntimeOwnerAccess $Destination $true
Expand-Archive -LiteralPath $zip -DestinationPath $Destination
foreach ($item in Get-ChildItem -LiteralPath $Destination -Force) {
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Embeddable runtime must contain only flat vendor files'
    }
    Set-RuntimeOwnerAccess $item.FullName $false
}
# Fail installation before compute is enabled if the owner's EXE/DLL/stdlib
# access or the host's execution policy prevents an ordinary vendor launch.
& (Join-Path $Destination 'python.exe') -I -B -c 'import json, statistics'
if ($LASTEXITCODE -ne 0) { throw 'Runtime owner execution check failed; inspect EXE/DLL/ZIP ACL and execution policy' }
Write-Host 'Set windows_worker_runtime to the absolute destination in agent.toml; run pcc-agent check before enabling compute.'
