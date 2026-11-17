#!/usr/bin/env bash
# publish_all_bots.sh
# Deploys functions/bot to all 6 bot Function Apps in Azure.

set -e

RESOURCE_GROUP="${1:-rg-ai-trading-bots}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
BOT_DIR="$REPO_ROOT/functions/bot"

echo "=========================================================="
echo " Discovering bot function apps in '$RESOURCE_GROUP'..."
echo "=========================================================="

APPS=$(az functionapp list --resource-group "$RESOURCE_GROUP" --query "[?contains(name, 'func-bot-')].name" -o tsv 2>/dev/null || true)

if [ -z "$APPS" ]; then
  echo "Error: No bot Function Apps found. Make sure you are logged into Azure ('az login')."
  exit 1
fi

echo "Found bots:"
echo "$APPS"
echo ""

cd "$BOT_DIR"

for APP in $APPS; do
  echo "----------------------------------------------------------"
  echo "Publishing to $APP ..."
  echo "----------------------------------------------------------"
  func azure functionapp publish "$APP" --python
done

echo "=========================================================="
echo "All bots published successfully!"
echo "=========================================================="
