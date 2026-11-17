# publish_all_bots.ps1
# Deploys functions/bot to all 6 bot Function Apps in Azure simultaneously or sequentially.

param(
    [string]$ResourceGroup = "rg-ai-trading-bots",
    [switch]$IncludeApi = $false
)

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Publishing AI Trading Bots to Azure Function Apps " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Ensure we are in functions/bot directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$botDir = Join-Path $repoRoot "functions/bot"
$apiDir = Join-Path $repoRoot "functions/api"

if (-not (Test-Path $botDir)) {
    Write-Error "Could not find functions/bot directory at $botDir"
    exit 1
}

# 2. Discover Bot Function Apps via Azure CLI
Write-Host "Discovering bot function apps in resource group '$ResourceGroup'..." -ForegroundColor Yellow
$apps = @()
try {
    $appsJson = az functionapp list --resource-group $ResourceGroup --query "[?contains(name, 'func-bot-')].name" -o json 2>$null
    if ($appsJson) {
        $apps = $appsJson | ConvertFrom-Json
    }
} catch {
    Write-Warning "Azure CLI query failed or az is not logged in."
}

# Fallback: if az CLI returned nothing or isn't installed, check terraform output or defaults
if (-not $apps -or $apps.Count -eq 0) {
    Write-Host "Querying terraform output for bot function names..." -ForegroundColor Yellow
    $tfDir = Join-Path $repoRoot "terraform"
    if (Test-Path $tfDir) {
        Push-Location $tfDir
        try {
            $tfJson = terraform output -json bot_function_names 2>$null
            if ($tfJson) {
                $tfMap = $tfJson | ConvertFrom-Json
                $apps = $tfMap.PSObject.Properties.Value
            }
        } catch {}
        Pop-Location
    }
}

if (-not $apps -or $apps.Count -eq 0) {
    Write-Error "No bot Function Apps found. Make sure you are logged into Azure ('az login') or specify function app names manually."
    exit 1
}

Write-Host "Found $($apps.Count) bot app(s) to publish:" -ForegroundColor Green
$apps | ForEach-Object { Write-Host " - $_" -ForegroundColor Green }
Write-Host ""

# 3. Publish functions/bot to each bot function app
Push-Location $botDir

$failed = @()
foreach ($app in $apps) {
    Write-Host "----------------------------------------------------------" -ForegroundColor Cyan
    Write-Host "Publishing to: $app ..." -ForegroundColor Cyan
    Write-Host "----------------------------------------------------------" -ForegroundColor Cyan
    
    func azure functionapp publish $app --python
    if ($LASTEXITCODE -ne 0) {
        $failed += $app
        Write-Warning "Publishing to $app failed with exit code $LASTEXITCODE"
    } else {
        Write-Host "Successfully published to $app!" -ForegroundColor Green
    }
}

Pop-Location

# 4. Optional: publish API
if ($IncludeApi) {
    Write-Host "----------------------------------------------------------" -ForegroundColor Cyan
    Write-Host "Publishing API Function App..." -ForegroundColor Cyan
    Write-Host "----------------------------------------------------------" -ForegroundColor Cyan
    
    $apiApp = (az functionapp list --resource-group $ResourceGroup --query "[?contains(name, 'func-api-') || contains(name, 'func-trading-api-')].name" -o tsv 2>$null)
    if ($apiApp) {
        Push-Location $apiDir
        func azure functionapp publish $apiApp.Trim() --python
        Pop-Location
    } else {
        Write-Warning "Could not automatically determine API function app name."
    }
}

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
if ($failed.Count -eq 0) {
    Write-Host "All $($apps.Count) bot(s) published successfully!" -ForegroundColor Green
} else {
    Write-Warning "Completed with errors. Failed apps: $($failed -join ', ')"
}
Write-Host "==========================================================" -ForegroundColor Cyan
