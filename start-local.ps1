# Sobe o ambiente local de teste (Docker + migracao + dashboard).
# Uso: clique com botao direito > Executar com PowerShell
#  ou:  powershell -ExecutionPolicy Bypass -File .\start-local.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "== automation-mass-push ==" -ForegroundColor Cyan

if (-not (Test-Path .env)) {
  Write-Host "ERRO: .env nao existe. Rode: copy .env.example .env e preencha SECRET_KEY e EVOLUTION_API_KEY"
  exit 1
}
if (-not (Test-Path .venv\Scripts\python.exe)) {
  Write-Host "Criando venv e instalando dependencias..."
  python -m venv .venv
  .\.venv\Scripts\python -m pip install -r requirements.txt
}

# Docker Desktop
$dockerOk = $false
docker info 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { $dockerOk = $true }
if (-not $dockerOk) {
  Write-Host "Subindo Docker Desktop..."
  $dd = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
  if (Test-Path $dd) { Start-Process $dd }
  for ($i = 1; $i -le 48; $i++) {
    Start-Sleep -Seconds 5
    docker info 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $dockerOk = $true; Write-Host "Docker OK ($($i*5)s)"; break }
    Write-Host "  aguardando Docker... $($i*5)s"
  }
}
if (-not $dockerOk) {
  Write-Host "ERRO: Docker Desktop nao subiu. Abra o Docker Desktop manualmente e rode este script de novo." -ForegroundColor Red
  exit 1
}

Write-Host "docker compose up -d ..."
# Este launcher roda o FastAPI no Windows, fora do container. Forca o webhook
# da Evolution a voltar para o host; sem isso respostas nao chegam a inbox.
$env:WEBHOOK_GLOBAL_ENABLED = "true"
$env:WEBHOOK_GLOBAL_URL = "http://host.docker.internal:8000/webhook/evolution"
docker compose up -d
if ($LASTEXITCODE -ne 0) {
  Write-Host "ERRO no docker compose" -ForegroundColor Red
  exit 1
}

Write-Host "Aguardando Postgres..."
for ($i = 0; $i -lt 40; $i++) {
  $h = docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' automation-mass-push-postgres-1 2>$null
  if ($h -eq "healthy") { break }
  Start-Sleep -Seconds 2
}

Write-Host "alembic upgrade head ..."
.\.venv\Scripts\python -m alembic upgrade head

# Libera porta 8000 se algo antigo ficou pendurado
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue |
  ForEach-Object { if ($_.OwningProcess -gt 0) { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } }
Start-Sleep -Seconds 1

Write-Host ""
Write-Host "Dashboard:  http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "Evolution:  http://localhost:8080"
Write-Host "Deixe esta janela aberta. CTRL+C encerra o dashboard."
Write-Host "Se der erro de QR: confirme que o Docker Desktop continua aberto."
Write-Host ""

.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
