@echo off
setlocal

echo ========================================================
echo Publishing code to func-api-v3-8u7515...
echo ========================================================

cd /d "c:\Users\Ricci\Documents\GitHub\AI-Trading-bots\functions\api"

echo.
echo Running func azure functionapp publish...
call func azure functionapp publish func-api-v3-8u7515 --build remote --python

echo.
echo ========================================================
echo Verifying leaderboard endpoint...
echo ========================================================
curl -s "https://func-api-v3-8u7515.azurewebsites.net/api/leaderboard"

echo.
echo Done!
pause
