# Bot-facing only -- reads NewsItems, writes plain "what happened"
# digests to the Digests container. Deliberately does NOT hunt for
# asymmetries; that broader, public-facing analysis is a separate job
# (see analysis.tf), and deliberately doesn't do retrieval over a
# separate search index either -- see the README for why that was
# evaluated and dropped. Runs on Gemini specifically, since this is the
# highest-frequency AI job in the whole system (every few hours, times
# every tracked ticker) and Gemini's free tier has the most daily
# headroom of the two providers in use.
resource "azurerm_linux_function_app" "synthesizer" {
  name                       = "func-synthesizer-${random_string.suffix.result}"
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
    "TRACKED_TICKERS"          = var.tickers
    "FINNHUB_API_KEY"          = var.finnhub_api_key
    "GEMINI_API_KEY"           = var.gemini_api_key
    "COSMOS_ENDPOINT"          = azurerm_cosmosdb_account.main.endpoint
    "COSMOS_KEY"               = azurerm_cosmosdb_account.main.primary_key
  }
}
