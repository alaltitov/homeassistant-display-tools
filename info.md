# Display Tools

This small integration allows you to receive forecasts, backend translations, and media player covers from Home Assistant for further use in ESPHome as sensors.


## Configuration from HACS:

Note: When the merge occurs in the main HACS branch, you will have to install manually beforehand

1. Go to Settings → Devices & Services → Add Integration
2. Search for "Display Tools"
3. Set the IP address for your Home Assistant server

## Usage

Now you can use the following services:

```
display_tools.get_forecasts
display_tools.get_raw_translations
display_tools.get_translations
display_tools.get_translations_esphome
display_tools.save_media_cover
```

And sensors:

```
sensor.display_tools                   # translations
sensor.display_tools_forecast_daily    # forecast daily
sensor.display_tools_forecast_hourly.  # forecast hourly
```

For more information, see the [README](https://github.com/alaltitov/homeassistant-display-tools/blob/main/README.md).