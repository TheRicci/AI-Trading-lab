#!/usr/bin/env bash
# publish_component.sh
# Deploys a specific Azure Function component based on what changed.
# Usage: ./scripts/publish_component.sh <component> [resource_group]
# Components: bot | api | analysis-report | daily-timer | all

set -eo pipefail

COMPONENT="${1:-all}"
RESOURCE_GROUP="${2:-${RESOURCE_GROUP:-rg-ai-trading-bots}}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=========================================================="
echo " Publishing Component: $COMPONENT"
echo " Resource Group:       $RESOURCE_GROUP"
echo "=========================================================="

deploy_app() {
  local app_name="$1"
  local src_dir="$2"
  
  echo "----------------------------------------------------------"
  echo "Deploying to Azure Function App: $app_name"
  echo "Source Directory:                $src_dir"
  echo "----------------------------------------------------------"
  
  (
    cd "$src_dir"
    func azure functionapp publish "$app_name" --build remote --python
  )
}

publish_bot() {
  local bot_dir="$REPO_ROOT/functions/bot"
  echo "Discovering bot function apps..."
  local apps
  apps=$(az functionapp list --resource-group "$RESOURCE_GROUP" --query "[?contains(name, 'func-bot-')].name" -o tsv 2>/dev/null || true)
  
  if [ -z "$apps" ]; then
    echo "Warning: No bot Function Apps found matching 'func-bot-' in $RESOURCE_GROUP."
    return 1
  fi
  
  echo "Found bot app(s):"
  echo "$apps"
  
  for app in $apps; do
    deploy_app "$app" "$bot_dir"
  done
}

publish_api() {
  local api_dir="$REPO_ROOT/functions/api"
  echo "Discovering API function app(s)..."
  local apps
  apps=$(az functionapp list --resource-group "$RESOURCE_GROUP" --query "[?contains(name, 'func-api-')].name" -o tsv 2>/dev/null || true)
  
  if [ -z "$apps" ]; then
    echo "Warning: No API Function Apps found matching 'func-api-' in $RESOURCE_GROUP."
    return 1
  fi
  
  echo "Found API app(s):"
  echo "$apps"
  
  for app in $apps; do
    deploy_app "$app" "$api_dir"
  done
}

publish_analysis() {
  local analysis_dir="$REPO_ROOT/functions/analysis-report"
  echo "Discovering analysis report function app(s)..."
  local apps
  apps=$(az functionapp list --resource-group "$RESOURCE_GROUP" --query "[?contains(name, 'func-analysis-report-')].name" -o tsv 2>/dev/null || true)
  
  if [ -z "$apps" ]; then
    echo "Warning: No Analysis Report Function Apps found in $RESOURCE_GROUP."
    return 1
  fi
  
  echo "Found Analysis Report app(s):"
  echo "$apps"
  
  for app in $apps; do
    deploy_app "$app" "$analysis_dir"
  done
}

publish_timer() {
  local timer_dir="$REPO_ROOT/functions/daily-timer"
  echo "Discovering daily timer function app(s)..."
  local apps
  apps=$(az functionapp list --resource-group "$RESOURCE_GROUP" --query "[?contains(name, 'func-daily-timer-')].name" -o tsv 2>/dev/null || true)
  
  if [ -z "$apps" ]; then
    echo "Warning: No Daily Timer Function Apps found in $RESOURCE_GROUP."
    return 1
  fi
  
  echo "Found Daily Timer app(s):"
  echo "$apps"
  
  for app in $apps; do
    deploy_app "$app" "$timer_dir"
  done
}

case "$COMPONENT" in
  bot|bots)
    publish_bot
    ;;
  api)
    publish_api
    ;;
  analysis|analysis-report)
    publish_analysis
    ;;
  timer|daily-timer)
    publish_timer
    ;;
  all)
    echo "Publishing ALL components..."
    publish_bot || true
    publish_api || true
    publish_analysis || true
    publish_timer || true
    ;;
  *)
    echo "Error: Unknown component '$COMPONENT'. Must be one of: bot, api, analysis-report, daily-timer, all"
    exit 1
    ;;
esac

echo "=========================================================="
echo "Deployment for $COMPONENT completed successfully!"
echo "=========================================================="
