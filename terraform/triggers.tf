# The ONLY publisher to the Event Grid topic. Fires at market open and
# close, nothing else -- see eventgrid.tf's comment for why.
resource "azurerm_linux_function_app" "daily_timer" {
  name                       = "func-daily-timer-${random_string.suffix.result}"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  service_plan_id            = azurerm_service_plan.consumption.id
  storage_account_name       = azurerm_storage_account.functions.name
  storage_account_access_key = azurerm_storage_account.functions.primary_access_key

  site_config {
    application_insights_connection_string = azurerm_application_insights.main.connection_string
    application_stack {
      python_version = "3.11"
    }
  }

  app_settings = {
    "FUNCTIONS_WORKER_RUNTIME" = "python"
    "EVENTGRID_TOPIC_ENDPOINT" = azurerm_eventgrid_topic.trading.endpoint
    "EVENTGRID_TOPIC_KEY"      = azurerm_eventgrid_topic.trading.primary_access_key
  }
}

# Pure collection -- polls Finnhub, writes to Cosmos. Dedup is a
# natural-key upsert using Finnhub's own article id (see
# functions/news-watcher), not AI Search. Does NOT publish to Event
# Grid or trigger anything. The synthesizer (synthesizer.tf) is what
# reads what this writes.
resource "azurerm_linux_function_app" "news_watcher" {
  name                       = "func-news-watcher-${random_string.suffix.result}"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  service_plan_id            = azurerm_service_plan.consumption.id
  storage_account_name       = azurerm_storage_account.functions.name
  storage_account_access_key = azurerm_storage_account.functions.primary_access_key

  site_config {
    application_insights_connection_string = azurerm_application_insights.main.connection_string
    application_stack {
      python_version = "3.11"
    }
  }

  app_settings = {
    "FUNCTIONS_WORKER_RUNTIME" = "python"
    "FINNHUB_API_KEY"          = var.finnhub_api_key
    "TRACKED_TICKERS"          = var.tickers
    "COSMOS_ENDPOINT"          = azurerm_cosmosdb_account.main.endpoint
    "COSMOS_KEY"               = azurerm_cosmosdb_account.main.primary_key
  }
}
