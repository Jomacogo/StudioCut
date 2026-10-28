# Publica el complemento StudioCut AI en GitHub.
#
# Requisitos:
#   1. GitHub CLI instalado (https://cli.github.com/).
#   2. Sesión iniciada:  gh auth login
#
# Uso (desde cualquier carpeta):
#   powershell -ExecutionPolicy Bypass -File complemento\publicar.ps1
#
# Qué hace:
#   1. Empaqueta el ZIP y actualiza plugin.json (SHA-256 + URL).
#   2. Copia el complemento a una carpeta temporal limpia.
#   3. Crea (o reutiliza) el repo Jomacogo/StudioCut-AI y sube main.
#   4. Crea la Release v<versión> con el ZIP adjunto.
#   5. Comprueba que el plugin.json publicado ya responde.

$ErrorActionPreference = 'Continue'

$repo = 'Jomacogo/StudioCut-AI'
$origen = $PSScriptRoot
$manifiesto = Join-Path $origen 'plugin.json'

function Fallar([string]$mensaje) {
    Write-Host "ERROR: $mensaje" -ForegroundColor Red
    exit 1
}

function Comprobar([string]$que) {
    if ($LASTEXITCODE -ne 0) { Fallar $que }
}

# 1. Comprobar gh -----------------------------------------------------------
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Fallar 'Falta GitHub CLI. Instálalo con: winget install GitHub.cli'
}
gh auth status *> $null
if ($LASTEXITCODE -ne 0) {
    Fallar 'GitHub CLI no tiene sesión. Ejecuta primero: gh auth login'
}

# 2. Empaquetar -------------------------------------------------------------
Write-Host 'Empaquetando el complemento...' -ForegroundColor Cyan
Push-Location $origen
try {
    python empaquetar.py
    Comprobar 'El empaquetado falló.'
} finally {
    Pop-Location
}

$datos = Get-Content $manifiesto -Raw -Encoding UTF8 | ConvertFrom-Json
$version = [string]$datos.version
$etiqueta = "v$version"
$zip = Join-Path $origen "dist\studiocut-ai-$version.zip"
if (-not (Test-Path $zip)) { Fallar "No se generó el ZIP: $zip" }
Write-Host "Versión: $version" -ForegroundColor Cyan

# 3. Copia limpia -----------------------------------------------------------
$temporal = Join-Path $env:TEMP "StudioCut-AI-$version"
if (Test-Path $temporal) {
    Remove-Item $temporal -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Path $temporal -Force | Out-Null
Get-ChildItem $origen -Force | Where-Object {
    $_.Name -notin @('dist', '__pycache__', '.git', '.pytest_cache')
} | ForEach-Object {
    Copy-Item $_.FullName -Destination $temporal -Recurse -Force
}

# 4. Repo y push ------------------------------------------------------------
Push-Location $temporal
try {
    git init -b main | Out-Null
    Comprobar 'No se pudo iniciar el repositorio temporal.'

    git add .
    Comprobar 'No se pudieron preparar los archivos.'

    git -c user.name='Jomacogo' -c user.email='Jomacogo@users.noreply.github.com' `
        commit -m "StudioCut AI $etiqueta" | Out-Null
    Comprobar 'No se pudo crear el commit.'

    gh repo view $repo *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Creando repositorio $repo..." -ForegroundColor Cyan
        gh repo create $repo --public `
            --description 'Complemento MCP de Studio Cut: guiones, imágenes y voz con IA.' `
            --source . --remote origin --push
        Comprobar 'No se pudo crear el repositorio.'
    } else {
        Write-Host 'El repositorio ya existe; subiendo main...' -ForegroundColor Cyan
        git remote remove origin 2> $null
        git remote add origin "https://github.com/$repo.git"
        Comprobar 'No se pudo configurar el remoto.'
        git push -u origin main
        Comprobar 'No se pudo subir main.'
    }
} finally {
    Pop-Location
}

# 5. Release ----------------------------------------------------------------
gh release view $etiqueta --repo $repo *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Creando Release $etiqueta..." -ForegroundColor Cyan
    gh release create $etiqueta $zip --repo $repo `
        --title "StudioCut AI $version" `
        --notes "Complemento MCP de Studio Cut $version. Instálalo desde la pestaña IA de la app."
    Comprobar 'No se pudo publicar la Release.'
} else {
    Write-Host 'La Release ya existe; reemplazando el ZIP...' -ForegroundColor Cyan
    gh release upload $etiqueta $zip --repo $repo --clobber
    Comprobar 'No se pudo reemplazar el ZIP de la Release.'
}

# 6. Comprobar --------------------------------------------------------------
Start-Sleep -Seconds 3
$url = "https://raw.githubusercontent.com/$repo/main/plugin.json"
try {
    $publicado = Invoke-RestMethod -Uri $url `
        -Headers @{ 'User-Agent' = 'StudioCut' } -TimeoutSec 30
    if ([string]$publicado.sha256 -ne [string]$datos.sha256) {
        Fallar 'El plugin.json publicado no coincide con el SHA-256 local.'
    }
    Write-Host 'OK: complemento publicado y verificado.' -ForegroundColor Green
    Write-Host "Manifiesto: $url"
    Write-Host 'Abre Studio Cut -> pestaña «🤖 IA» -> «Buscar e instalar».'
} catch {
    Fallar "El manifiesto aún no responde en $url ($($_.Exception.Message))"
}
