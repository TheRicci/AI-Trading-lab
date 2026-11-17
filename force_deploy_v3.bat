@echo off
setlocal EnableDelayedExpansion

echo ========================================================
echo   STEP 0 — Confirm the new file is on disk
echo ========================================================
echo Size of function_app.py (should be ~20KB, NOT 17KB):
for %%F in ("c:\Users\Ricci\Documents\GitHub\AI-Trading-bots\functions\api\function_app.py") do echo %%~zF bytes  (date: %%~tF)

echo.
echo Checking for api_version string in local file:
findstr "api_version" "c:\Users\Ricci\Documents\GitHub\AI-Trading-bots\functions\api\function_app.py"
if errorlevel 1 (
    echo ERROR: api_version NOT found in local file! Aborting.
    pause
    exit /b 1
)

echo.
echo ========================================================
echo   DEPLOY to func-api-v3-8u7515
echo ========================================================

set APP_NAME=func-api-v3-8u7515
set RESOURCE_GROUP=rg-ai-trading-bots
set SRC_DIR=c:\Users\Ricci\Documents\GitHub\AI-Trading-bots\functions\api

echo [1] Clearing WEBSITE_RUN_FROM_PACKAGE...
az functionapp config appsettings delete --name %APP_NAME% --resource-group %RESOURCE_GROUP% --setting-names WEBSITE_RUN_FROM_PACKAGE --output none 2>nul

echo [2] Setting build flags (disable Oryx caching)...
az functionapp config appsettings set --name %APP_NAME% --resource-group %RESOURCE_GROUP% --settings SCM_DO_BUILD_DURING_DEPLOYMENT=true ENABLE_ORYX_BUILD=true --output none

echo [3] Stopping app to flush old code...
az functionapp stop --name %APP_NAME% --resource-group %RESOURCE_GROUP% --output none

echo [4] Publishing via func CLI (bypasses zip deploy quirks)...
cd /d "%SRC_DIR%"
call func azure functionapp publish %APP_NAME% --build remote --python

echo [5] Starting app...
az functionapp start --name %APP_NAME% --resource-group %RESOURCE_GROUP% --output none

echo Waiting 25s for cold start...
timeout /t 25 /nobreak >nul

echo.
echo ========================================================
echo   VERIFY — Checking /api/leaderboard on v3
echo ========================================================
curl -s "https://%APP_NAME%.azurewebsites.net/api/leaderboard" > _resp.json
findstr "api_version" _resp.json && echo SUCCESS: api_version found! || echo FAIL: api_version not in response
del _resp.json 2>nul

echo.
pause
