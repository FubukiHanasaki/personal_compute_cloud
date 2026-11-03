# Dedicated, vendor-verified embeddable runtime. Author: FubukiHanasaki.
param([string]$Destination = 'tmp\windows-worker-runtime')
$ErrorActionPreference = 'Stop'
$zip = 'tmp\python-3.13.16-embed-amd64.zip'
$hash = '97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297'
if (Test-Path $Destination) { throw 'Destination must not exist; never reuse the Agent environment.' }
New-Item -ItemType Directory -Force tmp | Out-Null
Invoke-WebRequest 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-embed-amd64.zip' -OutFile $zip
if ((Get-FileHash $zip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $hash) { throw 'Python package checksum mismatch' }
Expand-Archive -LiteralPath $zip -DestinationPath $Destination
# Give only the runtime owner inherited access; each Worker gets a unique SID ACE.
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
icacls $Destination /inheritance:r /grant:r "*${sid}:(OI)(CI)F" /T /Q | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot secure runtime ACL' }
Write-Host 'Set windows_worker_runtime to the absolute destination in agent.toml; run pcc-agent check before enabling compute.'
