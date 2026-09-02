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
