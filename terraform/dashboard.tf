# Free_F1 is a genuine perpetual free SKU, not a 12-month trial construct.
# Sized for personal/small-scale use -- fine for you and some friends
# watching, worth checking current limits if you expect many simultaneous
# viewers later.
resource "azurerm_web_pubsub" "main" {
  name                = "wps-tradingbots-${random_string.suffix.result}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Free_F1"
  capacity            = 1
}

# Static Web Apps resource removed -- the frontend now deploys to Vercel
# (Next.js/TypeScript/React) instead of Azure. See frontend/README.md.

