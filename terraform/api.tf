# Serves the frontend. Deliberately exposes Analyses (public by
# design) and keeps the rest of the bot internals private.
resource "azurerm_linux_function_app" "api" {
  name                       = "func-api-${random_string.suffix.result}"
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
    # Loosened for a learning project so the Static Web App origin can
    # call this API. Locking this down to the actual frontend URL is a
    # good hardening exercise once everything is deployed and working.
    cors {
      allowed_origins = ["*"]
    }
  }

  app_settings = {
    "FUNCTIONS_WORKER_RUNTIME"    = "python"
    "COSMOS_ENDPOINT"             = azurerm_cosmosdb_account.main.endpoint
    "COSMOS_KEY"                  = azurerm_cosmosdb_account.main.primary_key
    "FINNHUB_API_KEY"             = var.finnhub_api_key
    "WEBPUBSUB_CONNECTION_STRING" = azurerm_web_pubsub.main.primary_connection_string
  }
}

# API v3 with the updated pricing engine and sanity checks
resource "azurerm_linux_function_app" "api_v3" {
  name                       = "func-api-v3-${random_string.suffix.result}"
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
    cors {
      allowed_origins = ["*"]
    }
  }

  app_settings = {
    "FUNCTIONS_WORKER_RUNTIME"    = "python"
    "COSMOS_ENDPOINT"             = azurerm_cosmosdb_account.main.endpoint
    "COSMOS_KEY"                  = azurerm_cosmosdb_account.main.primary_key
    "FINNHUB_API_KEY"             = var.finnhub_api_key
    "WEBPUBSUB_CONNECTION_STRING" = azurerm_web_pubsub.main.primary_connection_string
  }
}

