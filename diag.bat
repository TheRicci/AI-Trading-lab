@echo off
echo === GIT STATUS ===
git status

echo.
echo === function_app.py HASH (local) ===
certutil -hashfile "c:\Users\Ricci\Documents\GitHub\AI-Trading-bots\functions\api\function_app.py" SHA256

echo.
echo === FILES IN functions\api ===
dir "c:\Users\Ricci\Documents\GitHub\AI-Trading-bots\functions\api" /a

echo.
echo === .funcignore contents ===
type "c:\Users\Ricci\Documents\GitHub\AI-Trading-bots\functions\api\.funcignore" 2>nul || echo NO .funcignore found

echo.
echo === host.json contents ===
type "c:\Users\Ricci\Documents\GitHub\AI-Trading-bots\functions\api\host.json" 2>nul || echo NO host.json found

echo.
echo === SEARCHING for any OTHER function_app.py in the repo ===
dir /s /b "c:\Users\Ricci\Documents\GitHub\AI-Trading-bots\function_app.py" 2>nul

echo.
echo === SEARCHING for any zip or squashfs near functions\api ===
dir /s /b "c:\Users\Ricci\Documents\GitHub\AI-Trading-bots\functions" 2>nul | findstr /i ".zip .squashfs app.zip"

pause
