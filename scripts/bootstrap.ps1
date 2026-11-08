<#
.SYNOPSIS
    Bring the Enjaz chatbot up on a machine that has only a clone of this
    repository, and report honestly on what is still missing.

.DESCRIPTION
    A clone carries the code, the Compose file and all the migrations, so
    most of the system rebuilds itself. Four things it deliberately does not
    carry, because they are secret, licensed, or not ours to publish:

        infra/.env                                   every password and key
        D:\MOF_DataSample                            the document corpus
        Application/frontend/assets/fonts/*.ttf      licensed Monotype font
        the OCR cache inside Postgres                re-extracting it is billed

    This script checks for each, brings up the stack, applies the schema,
    and then stops. It does NOT run the ingestion pipeline: Stage 1 sends
    every document to Azure Document Intelligence, which is billed per page
    (about $1.50 for the 58-document corpus). Spending money is a decision,
    not a side effect of setup, so the script prints the command and leaves
    it to you.

    Safe to re-run. Every step either verifies what is already there or is
    itself idempotent.

    NOTE: this file is deliberately pure ASCII. Windows PowerShell 5.1 reads
    a .ps1 without a byte-order mark as ANSI, so a stray em dash in a string
    becomes mojibake and takes the parse down with it. Keep it that way.

.PARAMETER SkipBuild
    Start the containers from the images already present instead of
    rebuilding the two local ones. Faster on a second run; wrong after a
    code change, because a container runs the code baked into its image.

.PARAMETER CheckOnly
    Run the prerequisite checks and stop. Changes nothing, starts nothing.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -CheckOnly
    powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [switch]$CheckOnly
)

# 'Continue', not 'Stop', and deliberately. Almost every step here is a
# native command (docker, python, pytest), and under 'Stop' Windows
# PowerShell turns anything they write to stderr into a terminating
# NativeCommandError. A stopped Docker engine writes to stderr, so 'Stop'
# made this script die with a stack trace at the exact moment its job is to
# print "the engine is not running". Every native call below is checked by
# its exit code instead, which is what actually reports failure.
$ErrorActionPreference = 'Continue'
Set-StrictMode -Version Latest

$Root     = Split-Path -Parent $PSScriptRoot
$Infra    = Join-Path $Root 'infra'
$EnvFile  = Join-Path $Infra '.env'
$Example  = Join-Path $Infra '.env.example'
$FontDir  = Join-Path $Root 'Application\frontend\assets\fonts'
$Venv     = Join-Path $Root '.venv\Scripts\python.exe'

$script:Problems = @()
$script:Warnings = @()

function Step ($m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Good ($m) { Write-Host "   OK    $m" -ForegroundColor Green }
function Warn ($m) { Write-Host "   WARN  $m" -ForegroundColor Yellow
                     $script:Warnings += $m }
function Fail ($m) { Write-Host "   FAIL  $m" -ForegroundColor Red
                     $script:Problems += $m }
function Note ($m) { Write-Host "         $m" -ForegroundColor DarkGray }

# Reads one key out of infra/.env without sourcing the file. We never want
# 78 secrets loaded into this shell's environment just to read a path.
function Get-EnvValue ($name) {
    if (-not (Test-Path $EnvFile)) { return $null }
    $line = Select-String -Path $EnvFile -Pattern "^\s*$name\s*=(.*)$" |
            Select-Object -First 1
    if (-not $line) { return $null }
    return $line.Matches[0].Groups[1].Value.Trim()
}

Write-Host 'Enjaz chatbot - bootstrap' -ForegroundColor White
Note $Root

# ---------------------------------------------------------------------------
# 1. Prerequisites
# ---------------------------------------------------------------------------

Step 'Prerequisites'

# Docker. "docker info" rather than "docker --version", because the CLI
# answers for its own version with the engine stopped, and on this machine
# Docker Desktop does not start itself (INFRASTRUCTURE.md 4.4).
if (Get-Command docker -ErrorAction SilentlyContinue) {
    $server = docker info --format '{{.ServerVersion}}' 2>&1 | Select-Object -Last 1
    if ($LASTEXITCODE -eq 0) {
        Good "Docker engine $server"
    } else {
        Fail 'Docker is installed but the engine is not running. Open Docker Desktop first'
    }
} else {
    Fail 'Docker not found. Install Docker Desktop, and point its disk at a non-C: drive'
}

# Python. The venv is per-machine and must never be copied: it hardcodes
# absolute paths.
if (Test-Path $Venv) {
    Good "project venv: $(& $Venv --version)"
} else {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        Warn '.venv missing. Create it, then install requirements.txt into it'
        Note 'python -m venv .venv'
        Note '.venv\Scripts\python -m pip install -r requirements.txt'
        Note "system python is $(& python --version 2>&1); 3.13 is what this was built on"
    } else {
        Fail 'No Python found. Install 3.13, then create .venv'
    }
}

# ---------------------------------------------------------------------------
# 2. The four things a clone does not carry
# ---------------------------------------------------------------------------

Step 'What the clone does not carry'

if (Test-Path $EnvFile) {
    $secret = Get-EnvValue 'API_SESSION_SECRET'
    if ([string]::IsNullOrWhiteSpace($secret)) {
        Fail 'infra/.env has no API_SESSION_SECRET. Sign-in will refuse to start'
        Note 'generate one with: openssl rand -hex 32'
    } else {
        Good 'infra/.env present, session secret set'
    }
    foreach ($key in 'AZURE_OPENAI_LLM_API_KEY', 'AZURE_OPENAI_EMBEDDING_API_KEY',
                     'AZURE_DOCUMENT_INTELLIGENCE_KEY') {
        if ([string]::IsNullOrWhiteSpace((Get-EnvValue $key))) {
            Warn "$key is empty. The feature it serves will fail at the first call"
        }
    }
} else {
    Fail 'infra/.env missing. Copy it from the old machine, or start from .env.example'
    Note "template: $Example"
}

# The corpus. Its path comes from .env, because after a move it is the
# likeliest thing still pointing at the old machine's drive.
$docRoot = Get-EnvValue 'DOCUMENT_ROOT_HOST'
if ($docRoot) {
    if (Test-Path $docRoot) {
        $n = @(Get-ChildItem $docRoot -Recurse -File -ErrorAction SilentlyContinue).Count
        if ($n -ge 58) {
            Good "document corpus: $n files at $docRoot"
        } else {
            Warn "document corpus at $docRoot holds $n files; the built corpus is 58"
        }
    } else {
        Fail "DOCUMENT_ROOT_HOST points at $docRoot, which does not exist on this machine"
        Note 'copy the corpus across, then correct DOCUMENT_ROOT and DOCUMENT_ROOT_HOST in infra/.env'
    }
} elseif (Test-Path $EnvFile) {
    Fail 'DOCUMENT_ROOT_HOST is not set in infra/.env'
}

# The brand font. Absent, the UI silently falls back to a system face: a
# cosmetic failure that is easy to ship without noticing.
$fonts = @(Get-ChildItem $FontDir -Filter *.ttf -ErrorAction SilentlyContinue)
if ($fonts.Count -ge 3) {
    Good "brand font: $($fonts.Count) files"
} else {
    Warn 'DIN Next LT Arabic missing (licensed, gitignored). The UI falls back to a system font'
    Note "copy the .ttf files into $FontDir"
}

if ($script:Problems.Count -gt 0) {
    Write-Host "`nStopped. $($script:Problems.Count) thing(s) must be fixed first:" -ForegroundColor Red
    $script:Problems | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}

if ($CheckOnly) {
    Write-Host "`nChecks passed. Nothing was started (-CheckOnly)." -ForegroundColor Green
    if ($script:Warnings.Count -gt 0) {
        Write-Host "$($script:Warnings.Count) warning(s) above." -ForegroundColor Yellow
    }
    exit 0
}

# ---------------------------------------------------------------------------
# 3. The stack
# ---------------------------------------------------------------------------

Step 'Containers'

Push-Location $Infra
try {
    if ($SkipBuild) {
        Note 'docker compose up -d'
        docker compose up -d
    } else {
        Note 'docker compose up -d --build   (builds mof-enjaz-pipeline and mof-enjaz-chat)'
        docker compose up -d --build
    }
    if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed. See the output above' }

    # Postgres accepts connections a moment after the container reports Up,
    # and the migrations below connect immediately.
    Note 'waiting for Postgres to accept connections'
    $ready = $false
    foreach ($i in 1..30) {
        docker exec mof-postgres-spm pg_isready 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 2
    }
    if ($ready) {
        Good 'Postgres accepting connections'
    } else {
        throw 'Postgres did not become ready within 60s. Check: docker logs mof-postgres-spm'
    }
}
finally { Pop-Location }

# ---------------------------------------------------------------------------
# 4. The schema
# ---------------------------------------------------------------------------

Step 'Schema'

# apply_migrations.py deliberately needs no third-party packages, so it runs
# under whichever Python is available: the database can be rebuilt before
# the venv exists.
$py = if (Test-Path $Venv) { $Venv } else { 'python' }
& $py (Join-Path $Infra 'postgres-spm\apply_migrations.py')
if ($LASTEXITCODE -ne 0) { Write-Host 'Migrations failed.' -ForegroundColor Red; exit 1 }
& $py (Join-Path $Infra 'postgres-spm\apply_migrations.py') --status

# ---------------------------------------------------------------------------
# 5. Where this leaves you
# ---------------------------------------------------------------------------

Step 'State'

Push-Location $Infra
try { docker compose ps --format 'table {{.Name}}\t{{.Status}}' }
finally { Pop-Location }

if (Test-Path $Venv) {
    Note 'running the test suite'
    & $Venv -m pytest (Join-Path $Root 'tests') -q
}

Write-Host "`n== Done. The stack is up and the schema is applied" -ForegroundColor Cyan
if ($script:Warnings.Count -gt 0) {
    Write-Host "`n$($script:Warnings.Count) warning(s):" -ForegroundColor Yellow
    $script:Warnings | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
}

Write-Host @'

The database is seeded from the migrations: 9 projects, 4 users, 58 document
links and the four registers. What is NOT there yet is the extracted text and
the vectors, because building them spends money.

Check what would run, free:
    docker compose exec pipeline-worker python -m pipeline.ocr --dry-run

Then, deliberately (about $1.50 for 58 documents, roughly 154 pages):
    docker compose exec pipeline-worker python -m pipeline.tasks ingest --wait

If you carried the old machine's postgres-spm directory, the OCR cache came
with it and that run re-bills nothing: it will report 0 candidates.

Then open http://localhost:8000
'@ -ForegroundColor Gray
