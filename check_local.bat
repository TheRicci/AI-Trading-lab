@echo off
echo === CHECKING function_app.py CONTENT (first 60 lines) ===
type "c:\Users\Ricci\Documents\GitHub\AI-Trading-bots\functions\api\function_app.py" | more /E +1 | find /n "fetch_live_price" | head

echo.
echo === CHECKING if LOCAL file has asset_type parameter ===
findstr /c:"asset_type" "c:\Users\Ricci\Documents\GitHub\AI-Trading-bots\functions\api\function_app.py"

echo.
echo === CHECKING if LOCAL file has api_version ===
findstr /c:"api_version" "c:\Users\Ricci\Documents\GitHub\AI-Trading-bots\functions\api\function_app.py"

echo.
echo === CHECKING if LOCAL file has the OLD Coinbase-first bug ===
findstr /c:"Crypto via Coinbase" "c:\Users\Ricci\Documents\GitHub\AI-Trading-bots\functions\api\function_app.py"

echo.
echo === GIT DIFF of function_app.py (shows what changed from last commit) ===
git diff functions/api/function_app.py | head -100

pause
