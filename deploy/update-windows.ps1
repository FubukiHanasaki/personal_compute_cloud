# Author: FubukiHanasaki. Default is check-only; arguments pass through unchanged.
$ErrorActionPreference = 'Stop'
$pccRoot = Split-Path -Parent $PSScriptRoot
$pccScript = Join-Path $pccRoot 'scripts/update.py'
$pccPython = @('tmp/agent-venv/Scripts/python.exe', 'tmp/venv/Scripts/python.exe') | ForEach-Object { Join-Path $pccRoot $_ } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if ($pccPython) { & $pccPython $pccScript @args }
elseif (Get-Command py -ErrorAction SilentlyContinue) { & py -3 $pccScript @args }
elseif (Get-Command python -ErrorAction SilentlyContinue) { & python $pccScript @args }
else { throw 'Python 3.12+ is required' }
exit $LASTEXITCODE
