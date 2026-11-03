# Author: Fubuki. Run as a dedicated standard Windows user from the checkout.
$ErrorActionPreference = 'Stop'
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Use a standard non-administrator account' }
New-Item -ItemType Directory -Force tmp | Out-Null
py -3 -m venv tmp\agent-venv
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ is required' }
& tmp\agent-venv\Scripts\python.exe -m pip install .
if ($LASTEXITCODE -ne 0) { throw 'Installation failed' }
& tmp\agent-venv\Scripts\pcc-agent.exe init --config agent.toml
# Remove inherited broad read access from future credential directory.
icacls tmp /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F" | Out-Null
Write-Host 'Edit agent.toml, register, then run under this account. See docs/agent.md.'
