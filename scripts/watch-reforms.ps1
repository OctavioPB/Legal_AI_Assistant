<#
.SYNOPSIS
  Revisa las fuentes oficiales y carga las reformas (ADR-017). Lo ejecutan la tarea programada diaria
  (dev-up.ps1 -ScheduleReforms) y el arranque de la demo (si la última revisión tiene más de 20 h).

.DESCRIPTION
  Única pieza que usa la red, y solo hacia las URL oficiales registradas en app/ingestion/corpus.py.
  Si Docker o Ollama no están disponibles, lo anota en el log y termina sin error: la revisión se
  reintenta en la siguiente ejecución. Log: .run\watch-AAAAMMDD.log
#>
[CmdletBinding()]
param(
    [double]$IfStaleHours = 20,   # 0 = revisar siempre
    [switch]$DryRun               # solo comparar; no escribe nada
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$RunDir = Join-Path $Root ".run"
New-Item -ItemType Directory -Force $RunDir | Out-Null
$Log = Join-Path $RunDir ("watch-{0:yyyyMMdd}.log" -f (Get-Date))

function Log([string]$msg) { "{0:yyyy-MM-dd HH:mm:ss}  {1}" -f (Get-Date), $msg | Add-Content -Path $Log -Encoding UTF8 }

Set-Location $Root
Log "inicio (IfStaleHours=$IfStaleHours DryRun=$DryRun)"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Log "Docker no está instalado: se omite"; exit 0 }
# cmd /c: docker and uv write progress to stderr, which PowerShell 5.1 would turn into errors
cmd /c "docker compose up -d postgres >nul 2>&1"
if ($LASTEXITCODE -ne 0) { Log "Docker no responde (¿Docker Desktop cerrado?): se omite"; exit 0 }

$ollama = "http://127.0.0.1:11434/api/version"
$up = $false
try { $up = (Invoke-WebRequest -UseBasicParsing -Uri $ollama -TimeoutSec 3).StatusCode -eq 200 } catch { $up = $false }
if (-not $up -and (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath (Get-Command ollama).Source -ArgumentList "serve" -WindowStyle Hidden | Out-Null
    for ($i = 0; $i -lt 30 -and -not $up; $i++) {
        Start-Sleep -Seconds 2
        try { $up = (Invoke-WebRequest -UseBasicParsing -Uri $ollama -TimeoutSec 3).StatusCode -eq 200 } catch { $up = $false }
    }
}
if (-not $up) { Log "Ollama no responde (el embedder es necesario para cargar una reforma): se omite"; exit 0 }

$cmdline = "uv run python -m app.ingestion.watch"
if ($IfStaleHours -gt 0) { $cmdline += " --if-stale $IfStaleHours" }
if ($DryRun) { $cmdline += " --dry-run" }
$env:PYTHONIOENCODING = "utf-8"
cmd /c "$cmdline >> `"$Log`" 2>&1"
$code = $LASTEXITCODE
Log "fin (código $code)"
exit 0
