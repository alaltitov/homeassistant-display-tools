"""Constants for the Display Tools integration."""

DOMAIN = "display_tools"
STORAGE_KEY = f"{DOMAIN}.storage"
STORAGE_VERSION = 1
SENSOR_ENTITY_ID = "sensor.display_tools"

# Sensor IDs for weather forecasts
FORECAST_DAILY_SENSOR = "sensor.display_tools_forecasts_daily"
FORECAST_HOURLY_SENSOR = "sensor.display_tools_forecasts_hourly"

# Available translation categories
TRANSLATION_CATEGORIES = [
    'title',
    'state', 
    'entity',
    'entity_component',
    'exceptions',
    'config',
    'config_subentries',
    'config_panel',
    'options',
    'device_automation',
    'mfa_setup',
    'system_health',
    'application_credentials',
    'issues',
    'selector',
    'services'
]

# Cover image sizes
COVER_SIZES = {
    'small': (120, 120),
    'large': (160, 160)
}

# Weather forecast types
FORECAST_TYPES = ['daily', 'hourly']
