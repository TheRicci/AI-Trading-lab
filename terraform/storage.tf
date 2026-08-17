# The one piece that isn't officially "Always Free": every Function App on
# the Consumption plan requires a linked storage account to run at all.
# Cost is a few cents a month at most for this project's volume --
# unavoidable plumbing, not a design choice, and it doesn't scale with how
# long the system runs, only with actual storage/transactions.
resource "azurerm_storage_account" "functions" {
  name                     = "sttradingbots${random_string.suffix.result}"
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
}

# Shared Consumption plan (Y1 = pay-per-execution) for every function app
# in this project. Sharing one plan doesn't cost anything extra over
# separate plans -- Consumption billing is per-execution, not per-plan.
resource "azurerm_service_plan" "consumption" {
  name                = "asp-trading-bots"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  os_type             = "Linux"
  sku_name            = "Y1"
}
