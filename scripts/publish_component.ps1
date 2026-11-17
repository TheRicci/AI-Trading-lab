# publish_component.ps1
# Deploys a specific Azure Function component based on what changed.
# Usage: .\scripts\publish_component.ps1 -Component bot
# Components: bot | api | analysis-report | daily-timer | all

param(
    [ValidateSet("bot", "bots", "api", "analysis-report", "analysis", "daily-timer", "timer", "all")]
    [string]$Component = "all",
    [string]$ResourceGroup = "rg-ai-trading-bots"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Publishing Component: $Component" -ForegroundColor Cyan
Write-Host " Resource Group:       $ResourceGroup" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

function Deploy-App {
    param(
        [string]$AppName,
        [string]$SrcDir
    )
    Write-Host "----------------------------------------------------------" -ForegroundColor Yellow
    Write-Host "Deploying to Azure Function App: $AppName" -ForegroundColor Yellow
    Write-Host "Source Directory:                $SrcDir" -ForegroundColor Yellow
    Write-Host "----------------------------------------------------------" -ForegroundColor Yellow

    Push-Location $SrcDir
    try {
        func azure functionapp publish $AppName --build remote --python
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Deployment to $AppName failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
}

function Publish-Bot {
    $botDir = Join-Path $repoRoot "functions/bot"
    Write-Host "Discovering bot function apps..." -ForegroundColor Cyan
    $apps = az functionapp list --resource-group $ResourceGroup --query "[?contains(name, 'func-bot-')].name" -o json 2>$null | ConvertFrom-Json
    if (-not $apps -or $apps.Count -eq 0) {
        Write-Warning "No bot Function Apps found matching 'func-bot-' in $ResourceGroup."
        return
    }
    foreach ($app in $apps) {
        Deploy-App -AppName $app -SrcDir $botDir
    }
}

function Publish-Api {
    $apiDir = Join-Path $repoRoot "functions/api"
    Write-Host "Discovering API function apps..." -ForegroundColor Cyan
    $apps = az functionapp list --resource-group $ResourceGroup --query "[?contains(name, 'func-api-')].name" -o json 2>$null | ConvertFrom-Json
    if (-not $apps -or $apps.Count -eq 0) {
        Write-Warning "No API Function Apps found matching 'func-api-' in $ResourceGroup."
        return
    }
    foreach ($app in $apps) {
        Deploy-App -AppName $app -SrcDir $apiDir
    }
}

function Publish-Analysis {
    $analysisDir = Join-Path $repoRoot "functions/analysis-report"
    Write-Host "Discovering Analysis Report function apps..." -ForegroundColor Cyan
    $apps = az functionapp list --resource-group $ResourceGroup --query "[?contains(name, 'func-analysis-report-')].name" -o json 2>$null | ConvertFrom-Json
    if (-not $apps -or $apps.Count -eq 0) {
        Write-Warning "No Analysis Report Function Apps found in $ResourceGroup."
        return
    }
    foreach ($app in $apps) {
        Deploy-App -AppName $app -SrcDir $analysisDir
    }
}

function Publish-Timer {
    $timerDir = Join-Path $repoRoot "functions/daily-timer"
    Write-Host "Discovering Daily Timer function apps..." -ForegroundColor Cyan
    $apps = az functionapp list --resource-group $ResourceGroup --query "[?contains(name, 'func-daily-timer-')].name" -o json 2>$null | ConvertFrom-Json
    if (-not $apps -or $apps.Count -eq 0) {
        Write-Warning "No Daily Timer Function Apps found in $ResourceGroup."
        return
    }
    foreach ($app in $apps) {
        Deploy-App -AppName $app -SrcDir $timerDir
    }
}

switch ($Component) {
    { $_ -in "bot", "bots" } { Publish-Bot }
    "api" { Publish-Api }
    { $_ -in "analysis", "analysis-report" } { Publish-Analysis }
    { $_ -in "timer", "daily-timer" } { Publish-Timer }
    "all" {
        Publish-Bot
        Publish-Api
        Publish-Analysis
        Publish-Timer
    }
}

Write-Host "==========================================================" -ForegroundColor Green
Write-Host "Deployment for $Component completed successfully!" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
