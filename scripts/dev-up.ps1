<#
.SYNOPSIS
  Deja la demo local de Legal AI Assistant lista y la lanza (WS0.T10). Idempotente: correrlo dos
  veces no repite trabajo (no re-siembra el corpus, no recrea el usuario, no reinstala).

.DESCRIPTION
  1. Herramientas: Docker Desktop, uv, Node.js, Ollama (si faltan, pregunta y las instala con winget).
  2. Puertos: libera 8000/3100 si los ocupa una instancia anterior de ESTA app; si los ocupa otro
     programa, se detiene y dice cuál (nunca mata procesos ajenos). Verifica que todo escuche solo
     en 127.0.0.1: la demo no abre puertos al exterior ni crea reglas de firewall (ADR-014).
  3. Infraestructura: Postgres 16 + pgvector y Redis en Docker (solo loopback).
  4. Backend: uv sync, migraciones, doctor (invariantes), modelos de Ollama y sus pins.
  5. Datos: siembra el corpus si la base está vacía y crea el despacho/usuario demo.
  6. Web: npm ci y build si hace falta.
  7. Lanza API (:8000) y web (:3100) en segundo plano, espera a que respondan y abre el navegador.
  8. Reformas (ADR-017): si la última revisión de las fuentes oficiales tiene más de 20 h, la lanza en
     segundo plano (este mismo script con -WatchReforms). Con -ScheduleReforms queda además una tarea
     diaria de Windows (03:00, solo este usuario, sin permisos de administrador).

  -WatchReforms: revisa las fuentes oficiales y carga las reformas. Única pieza que usa la red, y solo
  hacia las URL oficiales de app/ingestion/corpus.py. Si Docker u Ollama no están disponibles, lo
  anota y termina sin error (se reintenta en la siguiente ejecución). Log: .run\watch-AAAAMMDD.log

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1            # preparar y lanzar (pregunta antes de descargar)
  powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1 -Yes       # sin preguntas
  powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1 -Status    # qué está corriendo
  powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1 -Stop      # cerrar API y web
  powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1 -Stop -All # además Docker y Ollama
  powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1 -ScheduleReforms    # revisión diaria de reformas
  powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1 -UnscheduleReforms  # quitarla
  powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1 -WatchReforms [-IfStaleHours 0] [-DryRun]  # revisar ahora
#>
[CmdletBinding()]
param(
    [switch]$Stop,              # detiene API y web (libera 8000 y 3100)
    [switch]$All,               # con -Stop: también detiene Postgres/Redis y el Ollama que lanzó este script
    [switch]$Status,            # solo informa
    [switch]$NoInstall,         # no instala herramientas que falten: solo avisa
    [switch]$Yes,               # no pregunta antes de descargar (instalaciones y modelos)
    [switch]$NoBrowser,         # no abre el navegador al final
    [switch]$Rebuild,           # fuerza npm run build
    [switch]$ResetDemoPassword, # genera una contraseña nueva para el usuario demo
    [switch]$ScheduleReforms,   # registra la revisión diaria de reformas (tarea de Windows del usuario)
    [switch]$UnscheduleReforms, # quita esa tarea
    [switch]$NoReformCheck,     # no lanza la revisión de reformas al arrancar
    [switch]$WatchReforms,      # solo revisa las fuentes oficiales y carga reformas (tarea diaria / arranque)
    [double]$IfStaleHours = 20, # con -WatchReforms: solo si la última revisión es más vieja (0 = siempre)
    [switch]$DryRun,            # con -WatchReforms: solo comparar; no escribe nada
    [int]$ApiPort = 8000,
    [int]$WebPort = 3100
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest es lentísimo con la barra de progreso en 5.1

$Root = Split-Path -Parent $PSScriptRoot
$Web = Join-Path $Root "web"
$RunDir = Join-Path $Root ".run"
$PidFile = Join-Path $RunDir "pids.json"
$CredFile = Join-Path $Root ".demo-credentials"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$OllamaUrl = "http://127.0.0.1:11434"
# ADR-014: embedder + redacción + utilidad. Los digests fijados están en config/models.local.yaml.
$Models = @("bge-m3", "gemma4:26b-a4b-it-qat", "qwen3:4b-instruct-2507-q4_K_M")
$InfraPorts = @{ 5432 = "Postgres"; 6379 = "Redis"; 11434 = "Ollama" }
$ReformTask = "LegalAI-RevisarReformas"
$Self = $PSCommandPath   # -WatchReforms re-invokes this same script (tarea diaria y arranque)

# ── utilidades ────────────────────────────────────────────────────────────────────────────────
function Write-Step([string]$msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg) { Write-Host "    [ok] $msg" -ForegroundColor Green }
function Write-Warn2([string]$msg) { Write-Host "    [!]  $msg" -ForegroundColor Yellow }
function Fail([string]$msg) { Write-Host ""; Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

function Invoke-Native {
    # Corre un ejecutable y falla con un mensaje claro si su código de salida no es 0.
    param([string]$What, [scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) { Fail "$What falló (código $LASTEXITCODE)." }
}

function Confirm-Download([string]$what) {
    # WS0.T10: nunca se descarga nada sin preguntar (salvo -Yes).
    if ($Yes) { return $true }
    $answer = Read-Host "    Descargar $what? [s/N]"
    return ($answer -match "^(s|si|sí|y|yes)$")
}

function Test-Command([string]$name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Update-PathFromRegistry {
    # Tras instalar con winget, el PATH nuevo no llega a esta sesión: lo recargamos.
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user;$env:USERPROFILE\.local\bin"
}

function Test-Http([string]$url, [int]$timeoutSec = 3) {
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec $timeoutSec
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 400)
    } catch { return $false }
}

function Wait-Http([string]$url, [int]$seconds, [string]$what) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Http $url) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Get-Listener([int]$port) {
    # Procesos que escuchan en el puerto, con su dirección local y línea de comandos.
    $conns = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    foreach ($c in $conns) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($c.OwningProcess)" -ErrorAction SilentlyContinue
        $cmd = ""
        $name = ""
        if ($p) { $cmd = [string]$p.CommandLine; $name = [string]$p.Name }
        [pscustomobject]@{ Port = $port; Address = $c.LocalAddress; Pid = $c.OwningProcess; Name = $name; CommandLine = $cmd }
    }
}

function Test-OurProcess($listener) {
    # "Nuestro" = lanzado desde este repositorio (uvicorn del .venv o next de web/). El python.exe
    # del venv relanza el intérprete base, cuya línea de comandos ya no menciona el repo: por eso
    # también se revisan hasta 4 procesos padre.
    $needle = $Root.ToLower()
    $procId = [int]$listener.Pid
    for ($i = 0; $i -lt 5 -and $procId -gt 0; $i++) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
        if (-not $p) { return $false }
        if ($p.CommandLine -and ([string]$p.CommandLine).ToLower().Contains($needle)) { return $true }
        $procId = [int]$p.ParentProcessId
    }
    return $false
}

function Start-Detached {
    # Lanza un proceso SIN heredar handles de esta consola (Win32_Process.Create). Start-Process los
    # hereda: si la salida del script va a un pipe (`| Tee-Object`), el pipe nunca se cierra y el
    # script no termina (medido). Además así el proceso sobrevive al cerrar la terminal.
    param([string]$Exe, [string[]]$ArgList, [string]$Cwd, [hashtable]$EnvVars, [string]$Out, [string]$Err)
    $sets = @($EnvVars.GetEnumerator() | ForEach-Object { "set `"$($_.Key)=$($_.Value)`"" }) -join " && "
    $argStr = @($ArgList | ForEach-Object { if ($_ -match "\s") { "`"$_`"" } else { $_ } }) -join " "
    $inner = "`"$Exe`" $argStr > `"$Out`" 2> `"$Err`""
    if ($sets) { $inner = "$sets && $inner" }
    $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
    $res = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = "cmd.exe /d /s /c `"$inner`""; CurrentDirectory = $Cwd; ProcessStartupInformation = $startup
    }
    if ($res.ReturnValue -ne 0) { Fail "No se pudo lanzar $Exe (Win32_Process.Create = $($res.ReturnValue))." }
    return [int]$res.ProcessId
}

function Stop-Tree([int]$processId) {
    & taskkill.exe /F /T /PID $processId *> $null
}

function Read-Pids {
    if (Test-Path $PidFile) {
        try { return Get-Content $PidFile -Raw | ConvertFrom-Json } catch { return $null }
    }
    return $null
}

function Save-Pids($data) {
    New-Item -ItemType Directory -Force $RunDir | Out-Null
    $data | ConvertTo-Json | Set-Content -Path $PidFile -Encoding UTF8
}

function Free-AppPort([int]$port, [string]$what) {
    # Cierra el puerto si lo tiene una instancia anterior de esta app; nunca toca procesos ajenos.
    foreach ($l in @(Get-Listener $port)) {
        if (Test-OurProcess $l) {
            Stop-Tree $l.Pid
            Write-Ok "puerto $port liberado (instancia anterior de $what, PID $($l.Pid))"
        } else {
            Fail ("El puerto $port ($what) lo usa otro programa: $($l.Name) (PID $($l.Pid)).`n" +
                  "       Ciérralo o usa otro puerto: -$(if ($what -eq 'API') {'ApiPort'} else {'WebPort'}) <n>.")
        }
    }
    $deadline = (Get-Date).AddSeconds(10)
    while (@(Get-Listener $port).Count -gt 0 -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 300 }
    if (@(Get-Listener $port).Count -gt 0) { Fail "El puerto $port sigue ocupado." }
}

function Assert-LoopbackOnly([int]$port, [string]$what) {
    # Nada de la demo debe ser alcanzable desde la red (privilegio abogado-cliente).
    foreach ($l in @(Get-Listener $port)) {
        if ($l.Address -notin @("127.0.0.1", "::1")) {
            Write-Warn2 "$what escucha en $($l.Address):$port (accesible desde la red). Debe ser 127.0.0.1."
            if ($what -eq "Ollama") { Write-Warn2 "Quita la variable OLLAMA_HOST=0.0.0.0 y reinicia Ollama." }
            return $false
        }
    }
    return $true
}

# ── reformas (ADR-017) ────────────────────────────────────────────────────────────────────────
function Test-ReformTask { return [bool](Get-ScheduledTask -TaskName $ReformTask -ErrorAction SilentlyContinue) }

function Invoke-ReformWatch {
    # Nunca falla: un problema se anota en el log y la revisión se reintenta en la siguiente ejecución.
    New-Item -ItemType Directory -Force $RunDir | Out-Null
    $log = Join-Path $RunDir ("watch-{0:yyyyMMdd}.log" -f (Get-Date))
    function Log([string]$msg) { "{0:yyyy-MM-dd HH:mm:ss}  {1}" -f (Get-Date), $msg | Add-Content -Path $log -Encoding UTF8 }
    Set-Location $Root
    Log "inicio (IfStaleHours=$IfStaleHours DryRun=$DryRun)"
    if (-not (Test-Command docker)) { Log "Docker no está instalado: se omite"; return }
    # cmd /c: docker y uv escriben progreso en stderr, que PowerShell 5.1 convertiría en errores
    cmd /c "docker compose up -d postgres >nul 2>&1"
    if ($LASTEXITCODE -ne 0) { Log "Docker no responde (¿Docker Desktop cerrado?): se omite"; return }
    $up = Test-Http "$OllamaUrl/api/version"
    if (-not $up -and (Test-Command ollama)) {
        Start-Process -FilePath (Get-Command ollama).Source -ArgumentList "serve" -WindowStyle Hidden | Out-Null
        for ($i = 0; $i -lt 30 -and -not $up; $i++) { Start-Sleep -Seconds 2; $up = Test-Http "$OllamaUrl/api/version" }
    }
    $env:PYTHONIOENCODING = "utf-8"
    if (-not $up) {
        Log "Ollama no responde (el embedder es necesario para cargar una reforma): se omite la revisión de leyes"
    } else {
        $cmdline = "uv run python -m app.ingestion.watch"
        if ($IfStaleHours -gt 0) { $cmdline += " --if-stale $IfStaleHours" }
        if ($DryRun) { $cmdline += " --dry-run" }
        cmd /c "$cmdline >> `"$log`" 2>&1"
        Log "leyes: fin (código $LASTEXITCODE)"
    }
    # ADR-027: noticias (no usa el modelo; después del vigilante, para incluir las reformas del día)
    $news = "uv run python -m app.news.pipeline"
    if ($DryRun) { $news += " --dry-run" }
    cmd /c "$news >> `"$log`" 2>&1"
    Log "noticias: fin (código $LASTEXITCODE)"
}

function Update-ReformTask {
    # Una tarea registrada antes de unir los scripts apunta a scripts\watch-reforms.ps1 (ya no existe).
    $t = Get-ScheduledTask -TaskName $ReformTask -ErrorAction SilentlyContinue
    if ($t -and -not (@($t.Actions) | Where-Object { $_.Arguments -match "-WatchReforms" })) {
        Register-ReformTask -Quiet
    }
}

function Register-ReformTask {
    param([switch]$Quiet)
    if (-not $Quiet) { Write-Step "Revisión diaria de reformas" }
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Self`" -WatchReforms"
    $trigger = New-ScheduledTaskTrigger -Daily -At "03:00"
    # StartWhenAvailable: si el equipo estaba apagado a las 03:00, corre al encenderse
    $opts = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    try {
        Register-ScheduledTask -TaskName $ReformTask -Action $action -Trigger $trigger -Settings $opts `
            -Description "Legal AI Assistant: revisa las fuentes oficiales y carga reformas (ADR-017)" -Force | Out-Null
    } catch { Fail "No se pudo registrar la tarea programada ($ReformTask): $($_.Exception.Message)" }
    if ($Quiet) { Write-Ok "tarea '$ReformTask' actualizada para usar dev-up.ps1 -WatchReforms"; return }
    Write-Ok "tarea '$ReformTask' registrada: todos los días a las 03:00 (o al encender el equipo si estaba apagado)"
    Write-Ok "solo descarga de las fuentes oficiales registradas; si una la rechaza, se anota y no se insiste"
    Write-Ok "para quitarla: dev-up.ps1 -UnscheduleReforms"
}

function Unregister-ReformTask {
    Write-Step "Revisión diaria de reformas"
    if (Test-ReformTask) {
        Unregister-ScheduledTask -TaskName $ReformTask -Confirm:$false
        Write-Ok "tarea '$ReformTask' eliminada"
    } else { Write-Ok "no había tarea programada" }
}

# ── -Status ───────────────────────────────────────────────────────────────────────────────────
function Show-Status {
    Write-Step "Estado"
    $rows = @(
        @{ n = "Postgres"; p = 5432; ok = { @(Get-Listener 5432).Count -gt 0 } },
        @{ n = "Redis"; p = 6379; ok = { @(Get-Listener 6379).Count -gt 0 } },
        @{ n = "Ollama"; p = 11434; ok = { Test-Http "$OllamaUrl/api/version" } },
        @{ n = "API"; p = $ApiPort; ok = { Test-Http "http://127.0.0.1:$ApiPort/health" } },
        @{ n = "Web"; p = $WebPort; ok = { Test-Http "http://127.0.0.1:$WebPort/login" } }
    )
    foreach ($r in $rows) {
        $up = & $r.ok
        $addr = (@(Get-Listener $r.p) | ForEach-Object { "$($_.Address)" }) -join ", "
        if ($up) { Write-Ok ("{0,-9} :{1,-6} arriba   ({2})" -f $r.n, $r.p, $addr) }
        else { Write-Warn2 ("{0,-9} :{1,-6} abajo" -f $r.n, $r.p) }
    }
    if (Test-ReformTask) { Write-Ok "revisión diaria de reformas: programada ($ReformTask)" }
    else { Write-Warn2 "revisión diaria de reformas: no programada (dev-up.ps1 -ScheduleReforms)" }
    if (Test-Path $RunDir) { Write-Host "    logs: $RunDir" }
}

# ── -Stop ─────────────────────────────────────────────────────────────────────────────────────
function Stop-App {
    Write-Step "Cerrando la aplicación"
    $saved = Read-Pids
    foreach ($port in @($ApiPort, $WebPort)) {
        foreach ($l in @(Get-Listener $port)) {
            if (Test-OurProcess $l) { Stop-Tree $l.Pid; Write-Ok "puerto $port cerrado (PID $($l.Pid))" }
            else { Write-Warn2 "puerto $port lo usa otro programa ($($l.Name)); no se toca" }
        }
    }
    if ($All) {
        Push-Location $Root
        try { Invoke-Native "docker compose stop" { docker compose stop postgres redis } } finally { Pop-Location }
        Write-Ok "Postgres y Redis detenidos (los datos se conservan en el volumen)"
        if ($saved -and $saved.PSObject.Properties.Name -contains "ollama" -and $saved.ollama) {
            Stop-Tree ([int]$saved.ollama); Write-Ok "Ollama detenido (lo había lanzado este script)"
        } else {
            Write-Ok "Ollama se deja corriendo (no lo lanzó este script)"
        }
    }
    if (Test-Path $PidFile) { Remove-Item $PidFile -Force }
    Show-Status
}

if ($WatchReforms) { Invoke-ReformWatch; exit 0 }
if ($ScheduleReforms) { Register-ReformTask; exit 0 }
if ($UnscheduleReforms) { Unregister-ReformTask; exit 0 }
Update-ReformTask
if ($Status) { Show-Status; exit 0 }
if ($Stop) { Stop-App; exit 0 }

Write-Host "Legal AI Assistant: demo local (todo en 127.0.0.1, nada sale de este equipo)" -ForegroundColor White
Set-Location $Root

# ── 1. herramientas ───────────────────────────────────────────────────────────────────────────
Write-Step "1/7 Herramientas"
$tools = @(
    @{ cmd = "docker"; id = "Docker.DockerDesktop"; name = "Docker Desktop" },
    @{ cmd = "uv"; id = "astral-sh.uv"; name = "uv" },
    @{ cmd = "node"; id = "OpenJS.NodeJS.LTS"; name = "Node.js LTS" },
    @{ cmd = "ollama"; id = "Ollama.Ollama"; name = "Ollama" }
)
$installed = $false
foreach ($t in $tools) {
    if (Test-Command $t.cmd) { Write-Ok "$($t.name)"; continue }
    if ($NoInstall) { Fail "Falta $($t.name). Instálalo (winget install $($t.id)) o corre sin -NoInstall." }
    if (-not (Test-Command "winget")) { Fail "Falta $($t.name) y no hay winget para instalarlo." }
    if (-not (Confirm-Download "e instalar $($t.name) con winget")) { Fail "Falta $($t.name); instálalo y vuelve a correr el script." }
    Write-Warn2 "instalando $($t.name) con winget..."
    Invoke-Native "winget install $($t.id)" {
        winget install --id $t.id -e --accept-package-agreements --accept-source-agreements --silent
    }
    $installed = $true
}
if ($installed) {
    Update-PathFromRegistry
    foreach ($t in $tools) {
        if (-not (Test-Command $t.cmd)) {
            Fail "$($t.name) se instaló pero no está en el PATH de esta sesión. Abre una terminal nueva y vuelve a correr el script."
        }
    }
}
$nodeMajor = [int](((node --version) -replace "^v", "").Split(".")[0])
if ($nodeMajor -lt 22) { Fail "Node $nodeMajor es muy viejo: se requiere 22 o superior." }
if (-not (Test-Path (Join-Path $Root ".env"))) {
    Copy-Item (Join-Path $Root ".env.example") (Join-Path $Root ".env")
    Write-Ok ".env creado desde .env.example (credenciales solo de desarrollo)"
}

# ── 2. puertos ────────────────────────────────────────────────────────────────────────────────
Write-Step "2/7 Puertos"
Free-AppPort $ApiPort "API"
Free-AppPort $WebPort "web"
# Si 5432/6379 los ocupa algo que no es Docker (p. ej. un Postgres instalado como servicio), compose fallará.
foreach ($port in @(5432, 6379)) {
    foreach ($l in @(Get-Listener $port)) {
        if ($l.Name -notmatch "docker|wslrelay|vpnkit|com\.docker") {
            Fail ("El puerto $port ($($InfraPorts[$port])) lo usa $($l.Name) (PID $($l.Pid)), no Docker.`n" +
                  "       Detén ese servicio o cambia LEGALAI_PG_PORT en .env.")
        }
    }
}
Write-Ok "8000/3100 libres; sin conflictos en 5432/6379"
Write-Ok "no se abren puertos en el firewall: todo escucha solo en 127.0.0.1"

# ── 3. infraestructura ────────────────────────────────────────────────────────────────────────
Write-Step "3/7 Docker: Postgres + Redis"
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    $exe = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $exe)) { Fail "Docker no responde y no encuentro Docker Desktop en $exe." }
    Write-Warn2 "Docker Desktop no está corriendo: iniciándolo (puede tardar 1-2 min)..."
    Start-Process $exe | Out-Null
    $deadline = (Get-Date).AddMinutes(4)
    do { Start-Sleep -Seconds 3; docker info *> $null } while ($LASTEXITCODE -ne 0 -and (Get-Date) -lt $deadline)
    if ($LASTEXITCODE -ne 0) { Fail "Docker Desktop no arrancó en 4 minutos (¿WSL2 habilitado?)." }
}
Invoke-Native "docker compose up" { docker compose up -d --wait postgres redis }
Assert-LoopbackOnly 5432 "Postgres" | Out-Null
Assert-LoopbackOnly 6379 "Redis" | Out-Null
Write-Ok "Postgres y Redis arriba (127.0.0.1)"

# ── 4. backend, modelos ───────────────────────────────────────────────────────────────────────
Write-Step "4/7 Backend y modelos locales"
Invoke-Native "uv sync" { uv sync --frozen }
Invoke-Native "alembic upgrade head" { uv run alembic upgrade head }
Write-Ok "dependencias y migraciones al día"

$startedOllama = $null
if (-not (Test-Http "$OllamaUrl/api/version")) {
    Write-Warn2 "Ollama no responde: iniciando 'ollama serve' en segundo plano..."
    New-Item -ItemType Directory -Force $RunDir | Out-Null
    $startedOllama = Start-Detached -Exe (Get-Command ollama).Source -ArgList @("serve") -Cwd $Root `
        -EnvVars @{ OLLAMA_HOST = "127.0.0.1:11434" } `
        -Out (Join-Path $RunDir "ollama.log") -Err (Join-Path $RunDir "ollama.err.log")
    if (-not (Wait-Http "$OllamaUrl/api/version" 60 "Ollama")) { Fail "Ollama no arrancó; revisa $RunDir\ollama.err.log" }
}
Assert-LoopbackOnly 11434 "Ollama" | Out-Null
$have = @((Invoke-RestMethod "$OllamaUrl/api/tags" -TimeoutSec 10).models | ForEach-Object { $_.name })
foreach ($m in $Models) {
    $full = $m
    if ($m -notmatch ":") { $full = "$m`:latest" }
    if ($have -contains $full) { Write-Ok "modelo $m"; continue }
    if (-not (Confirm-Download "el modelo $m (puede ser varios GB)")) { Fail "Falta el modelo $m (ollama pull $m)." }
    Write-Warn2 "descargando $m..."
    Invoke-Native "ollama pull $m" { ollama pull $m }
}
$env:PYTHONIOENCODING = "utf-8"
& uv run python -m app.orchestration.pins --check
if ($LASTEXITCODE -ne 0) {
    Fail ("Los modelos instalados no coinciden con config/models.local.yaml (otro digest = otro modelo).`n" +
          "       Eso es un cambio de modelo: requiere corrida golden y recalibrar tau (ADR-014). No se corrige solo.")
}
Invoke-Native "doctor" { uv run python -m app.doctor }

# ── 5. datos ──────────────────────────────────────────────────────────────────────────────────
Write-Step "5/7 Corpus y usuario demo"
$chunks = (docker compose exec -T postgres psql -U postgres -d legalai -tAc "SELECT count(*) FROM chunks" | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { Fail "No pude contar los chunks en Postgres." }
if ([int]$chunks -gt 0) {
    Write-Ok "corpus ya sembrado ($chunks chunks)"
} else {
    $raw = Join-Path $Root "data\corpus\raw"
    if (-not (Test-Path $raw) -or @(Get-ChildItem $raw -File -ErrorAction SilentlyContinue).Count -eq 0) {
        Fail "Faltan los documentos fuente en data\corpus\raw (no van en git). Fuentes y hashes: data\corpus\MANIFEST.md."
    }
    Write-Warn2 "base vacía: sembrando el corpus (~9 min la primera vez)..."
    Invoke-Native "seed" { uv run python -m app.ingestion.seed }
}
$demoArgs = @("run", "python", "-m", "app.demo")
if ($ResetDemoPassword) { $demoArgs += "--reset-password" }
$demoOut = (& uv @demoArgs | Out-String)
if ($LASTEXITCODE -ne 0) { Fail "app.demo falló." }
if ($demoOut -match "password:") {
    # Archivo ignorado por git; la contraseña solo existe aquí y como hash en la base.
    ($demoOut -split "`r?`n" | Where-Object { $_ -match "^(firm|email|password):" }) | Set-Content -Path $CredFile -Encoding UTF8
    Write-Ok "usuario demo creado; credenciales en .demo-credentials"
} elseif (Test-Path $CredFile) {
    Write-Ok "usuario demo ya existe; credenciales en .demo-credentials"
} else {
    Write-Warn2 "el usuario demo existe pero no hay .demo-credentials: corre con -ResetDemoPassword"
}

# ── 6. web ────────────────────────────────────────────────────────────────────────────────────
Write-Step "6/7 Web"
Push-Location $Web
try {
    $lock = Join-Path $Web "package-lock.json"
    $marker = Join-Path $Web "node_modules\.package-lock.json"
    if (-not (Test-Path $marker) -or (Get-Item $lock).LastWriteTime -gt (Get-Item $marker).LastWriteTime) {
        Invoke-Native "npm ci" { npm ci --no-audit --no-fund }
    }
    $buildId = Join-Path $Web ".next\BUILD_ID"
    $stale = $Rebuild -or -not (Test-Path $buildId)
    if (-not $stale) {
        $built = (Get-Item $buildId).LastWriteTime
        $newer = Get-ChildItem -Path (Join-Path $Web "src"), (Join-Path $Web "public") -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTime -gt $built } | Select-Object -First 1
        $cfg = Get-ChildItem $Web -File | Where-Object { $_.Name -match "^(next\.config|package\.json)" -and $_.LastWriteTime -gt $built }
        $stale = [bool]$newer -or [bool]$cfg
    }
    if ($stale) { Invoke-Native "npm run build" { npm run build } } else { Write-Ok "build al día" }
} finally { Pop-Location }

# ── 7. lanzar ─────────────────────────────────────────────────────────────────────────────────
Write-Step "7/7 Lanzando API y web"
New-Item -ItemType Directory -Force $RunDir | Out-Null
$apiPid = Start-Detached -Exe $Python -Cwd $Root `
    -ArgList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$ApiPort") `
    -EnvVars @{ PYTHONIOENCODING = "utf-8"; PYTHONWARNINGS = "ignore"; HF_HUB_DISABLE_SYMLINKS_WARNING = "1" } `
    -Out (Join-Path $RunDir "api.log") -Err (Join-Path $RunDir "api.err.log")
if (-not (Wait-Http "http://127.0.0.1:$ApiPort/health" 120 "API")) {
    Get-Content (Join-Path $RunDir "api.err.log") -Tail 15 -ErrorAction SilentlyContinue
    Fail "La API no respondió en 2 min (log arriba; completo en $RunDir\api.err.log)."
}
Write-Ok "API en http://127.0.0.1:$ApiPort"

$nextBin = Join-Path $Web "node_modules\next\dist\bin\next"
# INSECURE_LOCAL: cookie sin 'Secure' porque es http en loopback; sigue httpOnly + SameSite=Strict.
$webPid = Start-Detached -Exe (Get-Command node).Source -Cwd $Web `
    -ArgList @($nextBin, "start", "-H", "127.0.0.1", "-p", "$WebPort") `
    -EnvVars @{ API_URL = "http://127.0.0.1:$ApiPort"; INSECURE_LOCAL = "1" } `
    -Out (Join-Path $RunDir "web.log") -Err (Join-Path $RunDir "web.err.log")
if (-not (Wait-Http "http://127.0.0.1:$WebPort/login" 60 "web")) {
    Get-Content (Join-Path $RunDir "web.err.log") -Tail 15 -ErrorAction SilentlyContinue
    Fail "La web no respondió en 1 min ($RunDir\web.err.log)."
}
Write-Ok "web en http://127.0.0.1:$WebPort"
Assert-LoopbackOnly $ApiPort "API" | Out-Null
Assert-LoopbackOnly $WebPort "Web" | Out-Null

$prev = Read-Pids
$ollamaPid = $startedOllama
if (-not $ollamaPid -and $prev -and $prev.PSObject.Properties.Name -contains "ollama") { $ollamaPid = $prev.ollama }
Save-Pids @{ api = $apiPid; web = $webPid; ollama = $ollamaPid; started = (Get-Date).ToString("s") }

if (-not $NoReformCheck) {
    # ADR-017: en segundo plano y solo si la última revisión es vieja; no retrasa el arranque.
    Start-Detached -Exe (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -Cwd $Root `
        -ArgList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $Self, "-WatchReforms", "-IfStaleHours", "20") `
        -EnvVars @{ PYTHONIOENCODING = "utf-8" } `
        -Out (Join-Path $RunDir "watch.out.log") -Err (Join-Path $RunDir "watch.err.log") | Out-Null
    Write-Ok "revisión de reformas lanzada en segundo plano (log en $RunDir\watch-*.log)"
}

Write-Host ""
Write-Host "Listo." -ForegroundColor Green
Write-Host "  App:          http://127.0.0.1:$WebPort   (despacho 'demo', usuario owner@demo.local)"
Write-Host "  Credenciales: $CredFile"
Write-Host "  Logs:         $RunDir"
Write-Host "  Cerrar:       doble clic en `"Detener Legal AI.cmd`" (o dev-up.ps1 -Stop [-All])"
Write-Host "  Una respuesta tarda 1.5-4 min con el modelo de 26B; un tema fuera del corpus se rechaza en ~3 s."
if (-not (Test-ReformTask)) { Write-Host "  Reformas:     para revisarlas cada día: dev-up.ps1 -ScheduleReforms" }
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$WebPort" }
