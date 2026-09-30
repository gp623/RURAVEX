# MasteryFlow Windows PowerShell Runner
param (
    [string]$Action = "dev"
)

$ErrorActionPreference = "Stop"

function Show-Help {
    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host "  MasteryFlow Engine & Application Runner" -ForegroundColor Cyan
    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host "Usage: .\run.ps1 [quickstart | setup | seed | test | dev | clean]"
    Write-Host "  quickstart: Runs setup, seed, and dev in one command"
    Write-Host "  setup     : Installs Python requirements and npm dependencies"
    Write-Host "  seed      : Resets and seeds the SQLite database with 10 concepts, 50 questions, 5 archetypes"
    Write-Host "  test      : Runs the 23-test deterministic engine & API test suite"
    Write-Host "  dev       : Launches backend (http://127.0.0.1:8000) and frontend (http://localhost:5173)"
    Write-Host "  clean     : Removes SQLite database files and caches"
}

switch ($Action.ToLower()) {
    "quickstart" {
        Write-Host "Running full MasteryFlow Quickstart..." -ForegroundColor Cyan
        & $PSCommandPath setup
        & $PSCommandPath seed
        & $PSCommandPath dev
    }
    "setup" {
        Write-Host "Installing Python dependencies..." -ForegroundColor Green
        python -m pip install -r requirements.txt
        Write-Host "Installing Frontend dependencies..." -ForegroundColor Green
        npm.cmd --prefix frontend install
        Write-Host "Setup complete!" -ForegroundColor Green
    }
    "seed" {
        Write-Host "Seeding database..." -ForegroundColor Green
        python -m app.seed
    }
    "test" {
        Write-Host "Running deterministic test suite..." -ForegroundColor Green
        python -m pytest tests/ -v
    }
    "clean" {
        Write-Host "Cleaning database and temporary caches..." -ForegroundColor Yellow
        Remove-Item -Path "masteryflow.db*" -Force -ErrorAction SilentlyContinue
        Remove-Item -Path ".pytest_cache" -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "Clean complete." -ForegroundColor Green
    }
    "dev" {
        Write-Host "Starting MasteryFlow..." -ForegroundColor Cyan
        Write-Host "Backend API:  http://127.0.0.1:8000" -ForegroundColor Green
        Write-Host "Frontend App: http://localhost:5173" -ForegroundColor Green
        Write-Host "API Docs:     http://127.0.0.1:8000/docs" -ForegroundColor Green

        $backendProcess = Start-Process -FilePath "python" -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port 8000" -PassThru
        Start-Sleep -Seconds 2
        $frontendProcess = Start-Process -FilePath "npm.cmd" -ArgumentList "--prefix frontend run dev" -PassThru

        Write-Host "Both processes are running. Press Ctrl+C in this terminal to stop." -ForegroundColor Yellow
        try {
            while ($true) {
                Start-Sleep -Seconds 2
            }
        } finally {
            Write-Host "Stopping servers..." -ForegroundColor Yellow
            if ($backendProcess -and -not $backendProcess.HasExited) { Stop-Process -Id $backendProcess.Id -Force }
            if ($frontendProcess -and -not $frontendProcess.HasExited) { Stop-Process -Id $frontendProcess.Id -Force }
        }
    }
    default {
        Show-Help
    }
}
