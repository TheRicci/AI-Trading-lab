# AZ-104 domain: monitor and maintain Azure resources.
# Application Insights is what every function app below connects its
# logging to. Log Analytics' free tier includes a daily ingestion
# allowance -- see Azure Monitor's pricing page for the current amount.

resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-trading-bots"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
}

resource "azurerm_application_insights" "main" {
  name                = "appi-trading-bots"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "web"
}
