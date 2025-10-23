"""
Display Tools integration for Home Assistant.

This integration provides services to fetch translations from Home Assistant's backend,
process media player cover images for display devices, and normalize weather forecast data.
"""
from __future__ import annotations

import logging
import os
import aiohttp
import voluptuous as vol
from PIL import Image
from io import BytesIO
import json
from datetime import datetime, timezone

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.network import get_url
from homeassistant.components.frontend import async_get_translations

from .const import (
    DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
    SENSOR_ENTITY_ID,
    TRANSLATION_CATEGORIES,
    COVER_SIZES,
    FORECAST_TYPES,
    FORECAST_DAILY_SENSOR,
    FORECAST_HOURLY_SENSOR,
    CONF_BASE_URL,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Schema for get_raw_translations service
GET_RAW_TRANSLATIONS_SCHEMA = vol.Schema({
    vol.Required('language'): cv.string,
})

# Schema for get_translations service
GET_TRANSLATIONS_SCHEMA = vol.Schema({
    vol.Required('language'): cv.string,
    vol.Required('category'): vol.In(TRANSLATION_CATEGORIES),
    vol.Optional('keys'): vol.All(cv.ensure_list, [cv.string]),
})

# Schema for get_translations_esphome service
GET_TRANSLATIONS_ESPHOME_SCHEMA = vol.Schema({
    vol.Required('language'): cv.string,
    vol.Required('category'): vol.In(TRANSLATION_CATEGORIES),
    vol.Optional('keys'): vol.Any(
        vol.All(cv.ensure_list, [cv.string]),
        cv.string,
        list,
        None
    ),
})

# Schema for save_media_cover service
SAVE_MEDIA_COVER_SCHEMA = vol.Schema({
    vol.Required('entity_id'): cv.entity_id,
    vol.Required('size'): vol.In(['small', 'large']),
})

# Schema for get_forecasts service
GET_FORECASTS_SCHEMA = vol.Schema({
    vol.Required('entity_id'): cv.entity_id,
    vol.Required('type'): vol.In(FORECAST_TYPES),
})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Display Tools integration from configuration."""
    return True


def _get_base_url(hass: HomeAssistant, config_entry: ConfigEntry | None = None) -> str:
    """
    Get base URL for Home Assistant with multiple fallback strategies.
    
    Priority:
    1. User-configured URL from integration settings (if provided)
    2. get_url() helper (uses internal_url or external_url)
    3. hass.config.internal_url (local network URL)
    4. hass.config.external_url (internet URL)
    5. Fallback to localhost with detected port
    
    Args:
        hass: Home Assistant instance
        config_entry: Config entry with user settings (optional)
        
    Returns:
        str: Base URL without trailing slash
    """
    base_url = None
    source = "unknown"
    
    # Strategy 1: User-configured URL (highest priority)
    if config_entry:
        user_url = config_entry.data.get(CONF_BASE_URL)
        if user_url:
            base_url = user_url.rstrip('/')
            source = "user_config"
            _LOGGER.debug(f"Using user-configured base_url: {base_url}")
            return base_url
    
    # Strategy 2: Use get_url() helper (recommended by HA)
    try:
        base_url = get_url(hass)
        if base_url:
            base_url = base_url.rstrip('/')
            source = "get_url_helper"
            _LOGGER.debug(f"Using get_url() helper: {base_url}")
            return base_url
    except Exception as e:
        _LOGGER.debug(f"get_url() helper failed: {e}")
    
    # Strategy 3: Internal URL (local network)
    if hass.config.internal_url:
        base_url = hass.config.internal_url.rstrip('/')
        source = "internal_url"
        _LOGGER.debug(f"Using internal_url: {base_url}")
        return base_url
    
    # Strategy 4: External URL (internet)
    if hass.config.external_url:
        base_url = hass.config.external_url.rstrip('/')
        source = "external_url"
        _LOGGER.debug(f"Using external_url: {base_url}")
        return base_url
    
    # Strategy 5: Fallback to localhost (last resort)
    try:
        port = hass.http.server_port
        base_url = f"http://localhost:{port}"
        source = "localhost_fallback"
        _LOGGER.debug(f"Using localhost fallback: {base_url}")
        return base_url
    except Exception as e:
        _LOGGER.error(f"Failed to get server port: {e}")
        base_url = "http://localhost:8123"
        source = "hardcoded_fallback"
        _LOGGER.warning(f"Using hardcoded fallback: {base_url}")
        return base_url


async def _fetch_translations_for_category(hass: HomeAssistant, language: str, category: str) -> dict:
    """
    Fetch all translations for a specific category and language.
    
    Args:
        hass: Home Assistant instance
        language (str): Language code (e.g., 'ru', 'en')
        category (str): Translation category (e.g., 'state', 'entity_component')
        
    Returns:
        dict: All translations for the category
    """
    try:
        translations = await async_get_translations(hass, language, category)
        return translations
    except Exception as e:
        _LOGGER.error(f"Error fetching translations for {language}.{category}: {e}")
        return {}


async def _filter_translations_by_keys(translations: dict, keys: list[str]) -> dict:
    """
    Filter translations dictionary by specific keys.
    
    Args:
        translations (dict): Full translations dictionary
        keys (list): List of keys to filter by
        
    Returns:
        dict: Filtered translations
    """
    if not keys:
        return translations
    
    filtered = {}
    for key in keys:
        if key in translations:
            filtered[key] = translations[key]
        else:
            filtered[key] = key  # Fallback to key itself
    
    return filtered


async def _download_and_process_cover(hass: HomeAssistant, entity_id: str, size: str) -> bool:
    """
    Download and process media player cover image.
    
    Args:
        hass: Home Assistant instance
        entity_id (str): Media player entity ID
        size (str): Size preset ('small' or 'large')
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Get entity state
        state = hass.states.get(entity_id)
        if not state:
            _LOGGER.error(f"Entity {entity_id} not found")
            return False
        
        # Get image URL from entity_picture attribute
        entity_picture = state.attributes.get('entity_picture')
        if not entity_picture:
            _LOGGER.error(f"No entity_picture found for {entity_id}")
            return False
        
        # Build full URL if relative path
        if entity_picture.startswith('/'):
            # Get config entry for user settings
            config_entry = hass.data[DOMAIN].get("config_entry")
            
            # Get base URL using multi-strategy approach
            base_url = _get_base_url(hass, config_entry)
            
            image_url = f"{base_url}{entity_picture}"
            _LOGGER.info(f"Built image URL: {image_url}")
        else:
            image_url = entity_picture
            _LOGGER.info(f"Using absolute image URL: {image_url}")
        
        # Get target size
        target_size = COVER_SIZES[size]
        
        # Download image
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as response:
                if response.status != 200:
                    _LOGGER.error(f"Failed to download image from {image_url}, status: {response.status}")
                    return False
                
                image_data = await response.read()
        
        # Process image with PIL
        try:
            # Open image
            img = Image.open(BytesIO(image_data))
            
            # Convert to RGB if needed (for JPEG)
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            
            # Resize with aspect ratio preserved
            img.thumbnail(target_size, Image.Resampling.LANCZOS)
            
            # Create new image with exact dimensions and center content
            new_img = Image.new('RGB', target_size, (0, 0, 0))
            
            # Calculate position for centering
            x = (target_size[0] - img.width) // 2
            y = (target_size[1] - img.height) // 2
            
            # Paste image centered
            new_img.paste(img, (x, y))
            
            # Create directory if not exists
            output_dir = "/config/www/display_tools"
            os.makedirs(output_dir, exist_ok=True)
            
            # Save image
            output_path = os.path.join(output_dir, "cover.jpeg")
            new_img.save(output_path, "JPEG", quality=85)
            
            _LOGGER.info(f"Successfully saved cover image to {output_path} with size {target_size}")
            return True
            
        except Exception as e:
            _LOGGER.error(f"Error processing image: {e}")
            return False
            
    except Exception as e:
        _LOGGER.error(f"Error in _download_and_process_cover: {e}")
        return False


def _filter_forecast_attributes(forecast_item: dict) -> dict:
    """
    Filter forecast item to keep only essential attributes.
    Normalizes datetime to ISO string format in UTC.
    
    Args:
        forecast_item (dict): Original forecast item
        
    Returns:
        dict: Filtered forecast with only condition, datetime (as UTC string), temperature
    """
    # Get datetime value
    dt = forecast_item.get("datetime", "")
    
    # Normalize datetime to UTC ISO string
    if dt:
        if isinstance(dt, datetime):
            # If datetime object, convert to UTC
            if dt.tzinfo is None:
                # If no timezone, assume UTC
                dt_utc = dt.replace(tzinfo=timezone.utc)
            else:
                # Convert to UTC
                dt_utc = dt.astimezone(timezone.utc)
            
            dt_str = dt_utc.isoformat()
            _LOGGER.debug(f"Converted datetime object to UTC: {dt} → {dt_str}")
            
        elif isinstance(dt, str):
            # If string, parse and convert to UTC
            try:
                # Parse ISO string
                dt_obj = datetime.fromisoformat(dt.replace('Z', '+00:00'))
                
                if dt_obj.tzinfo is None:
                    dt_utc = dt_obj.replace(tzinfo=timezone.utc)
                else:
                    dt_utc = dt_obj.astimezone(timezone.utc)
                
                dt_str = dt_utc.isoformat()
                _LOGGER.debug(f"Converted datetime string to UTC: {dt} → {dt_str}")
                
            except Exception as e:
                _LOGGER.warning(f"Failed to parse datetime string: {dt}, error: {e}")
                dt_str = dt  # Keep as is
        else:
            _LOGGER.warning(f"Unknown datetime format: {type(dt)} = {dt}")
            dt_str = str(dt)
    else:
        dt_str = ""
    
    return {
        "condition": forecast_item.get("condition", "sunny"),
        "datetime": dt_str,  # Always UTC ISO string
        "temperature": forecast_item.get("temperature", 0.0),
    }


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Display Tools from a config entry."""
    
    # Initialize hass.data
    hass.data.setdefault(DOMAIN, {})
    
    # Store config entry for accessing user settings
    hass.data[DOMAIN]["config_entry"] = entry
    
    # Initialize storage
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    hass.data[DOMAIN]["store"] = store
    
    # Log current base URL configuration
    base_url = _get_base_url(hass, entry)
    _LOGGER.info(f"Display Tools initialized with base_url: {base_url}")
    
    # Load stored data
    stored_data = await store.async_load()
    
    if stored_data:
        # Restore sensor with stored data
        attributes = {
            "friendly_name": "Display Tools",
            "icon": "mdi:monitor-dashboard",
            "language": stored_data.get("language", "unknown"),
            "category": stored_data.get("category", "unknown"),
            "translations_count": stored_data.get("translations_count", 0),
            "available_categories": TRANSLATION_CATEGORIES,
            "available_cover_sizes": list(COVER_SIZES.keys()),
            "requested_keys_count": stored_data.get("requested_keys_count", 0),
            "base_url": base_url,
        }
        
        # Add grouped translations
        grouped_translations = stored_data.get("grouped_translations", {})
        for component, component_translations in grouped_translations.items():
            # Store as JSON string for ESPHome
            attributes[component] = json.dumps(component_translations, ensure_ascii=False)
        
        hass.states.async_set(
            SENSOR_ENTITY_ID,
            stored_data.get("language", "empty"),
            attributes
        )
        
        _LOGGER.info(f"Restored Display Tools sensor with {len(grouped_translations)} component groups")
        
        # Restore forecast sensors
        for forecast_type in FORECAST_TYPES:
            forecast_key = f"forecast_{forecast_type}"
            if forecast_key in stored_data:
                forecast_info = stored_data[forecast_key]
                sensor_id = FORECAST_DAILY_SENSOR if forecast_type == "daily" else FORECAST_HOURLY_SENSOR
                
                attributes = {
                    "friendly_name": f"Display Tools Forecasts ({forecast_type.capitalize()})",
                    "icon": "mdi:weather-partly-cloudy",
                    "entity_id": forecast_info.get("entity_id"),
                    "type": forecast_info.get("type"),
                    "count": forecast_info.get("count", 0),
                    "forecasts": forecast_info.get("forecasts", []),
                }
                
                hass.states.async_set(
                    sensor_id,
                    forecast_info.get("count", 0),
                    attributes
                )
                
                _LOGGER.info(f"Restored {sensor_id} with {forecast_info.get('count', 0)} forecast items")
        
    else:
        # Create initial sensor state (empty)
        hass.states.async_set(
            SENSOR_ENTITY_ID,
            "empty",
            {
                "friendly_name": "Display Tools",
                "icon": "mdi:monitor-dashboard",
                "available_categories": TRANSLATION_CATEGORIES,
                "available_cover_sizes": list(COVER_SIZES.keys()),
                "base_url": base_url,
            }
        )
    
    async def handle_get_raw_translations(call: ServiceCall) -> ServiceResponse:
        """Handle the get_raw_translations service call - returns ALL translations for language."""
        language = call.data.get("language")
        
        try:
            result = {}
            
            # Get translations for all categories
            for category in TRANSLATION_CATEGORIES:
                translations = await _fetch_translations_for_category(hass, language, category)
                if translations:
                    result[category] = translations
            
            return {
                "language": language,
                "categories": result,
                "total_categories": len(result)
            }
            
        except Exception as e:
            _LOGGER.error(f"Error in get_raw_translations service: {e}")
            return {
                "language": language,
                "categories": {},
                "error": str(e)
            }
    
    async def handle_get_translations(call: ServiceCall) -> ServiceResponse:
        """Handle the get_translations service call - returns translations for specific category."""
        language = call.data.get("language")
        category = call.data.get("category")
        keys = call.data.get("keys")
        
        try:
            # Get all translations for category
            translations = await _fetch_translations_for_category(hass, language, category)
            
            # Filter by keys if specified
            if keys:
                translations = await _filter_translations_by_keys(translations, keys)
            
            return {
                "language": language,
                "category": category,
                "translations": translations,
                "total_translations": len(translations)
            }
            
        except Exception as e:
            _LOGGER.error(f"Error in get_translations service: {e}")
            return {
                "language": language,
                "category": category,
                "translations": {},
                "error": str(e)
            }
    
    async def handle_get_translations_esphome(call: ServiceCall) -> None:
        """Handle the get_translations_esphome service call - updates sensor for ESPHome."""
        language = call.data.get("language")
        category = call.data.get("category")
        keys_raw = call.data.get("keys")
        
        # Process keys with ESPHome specifics handling
        keys = None
        if keys_raw is not None:
            try:
                if isinstance(keys_raw, list) and len(keys_raw) == 1:
                    # ESPHome sends list with single element
                    single_item = keys_raw[0]
                    
                    if isinstance(single_item, str):
                        # Try JSON first
                        try:
                            keys = json.loads(single_item)
                        except json.JSONDecodeError:
                            # If not JSON, split by comma and clean
                            keys = [k.strip() for k in single_item.split(',') if k.strip()]
                    else:
                        keys = [str(single_item)]
                        
                elif isinstance(keys_raw, list):
                    # Regular list
                    keys = keys_raw
                    
                elif isinstance(keys_raw, str):
                    # String directly
                    try:
                        keys = json.loads(keys_raw)
                    except json.JSONDecodeError:
                        keys = [k.strip() for k in keys_raw.split(',') if k.strip()]
                        
                elif hasattr(keys_raw, '__iter__') and not isinstance(keys_raw, (str, bytes)):
                    # Iterable object
                    keys = list(keys_raw)
                else:
                    keys = [str(keys_raw)]
                    
            except Exception as e:
                _LOGGER.error(f"Error processing keys: {e}")
                keys = None
        
        try:
            # Get all translations for category
            translations = await _fetch_translations_for_category(hass, language, category)
            
            # Filter by keys if specified
            if keys:
                translations = await _filter_translations_by_keys(translations, keys)
            
            # Group translations by components
            grouped_translations = {}
            for key, value in translations.items():
                # Extract component from key (e.g., vacuum from component.vacuum.entity_component._.state.cleaning)
                parts = key.split('.')
                if len(parts) >= 2 and parts[0] == 'component':
                    component = parts[1]  # vacuum, cover, climate, weather
                    # Extract last part as key (cleaning, opening, heating, etc.)
                    final_key = parts[-1]
                    
                    if component not in grouped_translations:
                        grouped_translations[component] = {}
                    grouped_translations[component][final_key] = value
            
            # Get current data from storage
            store = hass.data[DOMAIN]["store"]
            stored_data = await store.async_load() or {}
            
            # Update stored data
            stored_data.update({
                "language": language,
                "category": category,
                "grouped_translations": grouped_translations,
                "translations_count": len(translations),
                "requested_keys_count": len(keys) if keys else 0,
            })
            
            # Save to storage
            await store.async_save(stored_data)
            
            # Get current base URL
            base_url = _get_base_url(hass, entry)
            
            # Create sensor attributes
            attributes = {
                "friendly_name": "Display Tools",
                "icon": "mdi:monitor-dashboard",
                "language": language,
                "category": category,
                "translations_count": len(translations),
                "available_categories": TRANSLATION_CATEGORIES,
                "available_cover_sizes": list(COVER_SIZES.keys()),
                "requested_keys_count": len(keys) if keys else 0,
                "base_url": base_url,
            }
            
            # Add grouped translations as separate attributes (JSON strings)
            for component, component_translations in grouped_translations.items():
                attributes[component] = json.dumps(component_translations, ensure_ascii=False)
            
            # Update sensor
            hass.states.async_set(
                SENSOR_ENTITY_ID,
                language,  # State = active language
                attributes
            )
            
            _LOGGER.info(f"Updated Display Tools sensor with {len(grouped_translations)} component groups for language {language}")
            
        except Exception as e:
            _LOGGER.error(f"Error in get_translations_esphome service: {e}")
            
            # Set error state on failure
            hass.states.async_set(
                SENSOR_ENTITY_ID,
                "error",
                {
                    "friendly_name": "Display Tools",
                    "icon": "mdi:monitor-off",
                    "error": str(e),
                    "available_categories": TRANSLATION_CATEGORIES,
                    "available_cover_sizes": list(COVER_SIZES.keys()),
                }
            )
    
    async def handle_save_media_cover(call: ServiceCall) -> None:
        """Handle the save_media_cover service call - downloads and processes media cover."""
        entity_id = call.data.get("entity_id")
        size = call.data.get("size")
        
        _LOGGER.info(f"Processing cover for {entity_id} with size {size}")
        
        try:
            success = await _download_and_process_cover(hass, entity_id, size)
            
            if success:
                _LOGGER.info(f"Successfully processed cover for {entity_id}")
            else:
                _LOGGER.error(f"Failed to process cover for {entity_id}")
                
        except Exception as e:
            _LOGGER.error(f"Error in save_media_cover service: {e}")
    
    
    async def handle_get_forecasts(call: ServiceCall) -> None:
        """Handle the get_forecasts service call - gets weather forecasts and updates sensor."""
        entity_id = call.data.get("entity_id")
        forecast_type = call.data.get("type")
        
        _LOGGER.info(f"Getting {forecast_type} forecast for {entity_id}")
        
        try:
            # Call weather.get_forecasts service
            response = await hass.services.async_call(
                "weather",
                "get_forecasts",
                {
                    "entity_id": entity_id,
                    "type": forecast_type,
                },
                blocking=True,
                return_response=True,
            )
            
            _LOGGER.debug(f"Raw response from weather.get_forecasts: {response}")
            
            # Extract forecast data
            # Response structure: {entity_id: {forecast: [...]}}
            forecast_data = None
            if response and entity_id in response:
                forecast_data = response[entity_id].get("forecast", [])
            
            if not forecast_data:
                _LOGGER.error(f"No forecast data received for {entity_id}")
                return
            
            # Limit to 12 forecasts
            forecast_data = forecast_data[:12]
            
            # Filter attributes (keep only condition, datetime, temperature)
            filtered_forecasts = [_filter_forecast_attributes(item) for item in forecast_data]
            
            _LOGGER.info(f"Filtered {len(filtered_forecasts)} forecasts with essential attributes only")
            
            # Determine which sensor to update
            sensor_id = FORECAST_DAILY_SENSOR if forecast_type == "daily" else FORECAST_HOURLY_SENSOR
            
            # Get current data from storage
            store = hass.data[DOMAIN]["store"]
            stored_data = await store.async_load() or {}
            
            # Save forecast data
            forecast_key = f"forecast_{forecast_type}"
            stored_data[forecast_key] = {
                "entity_id": entity_id,
                "type": forecast_type,
                "forecasts": filtered_forecasts,
                "count": len(filtered_forecasts),
            }
            
            # Save to storage
            await store.async_save(stored_data)
            
            # Create sensor attributes
            attributes = {
                "friendly_name": f"Display Tools Forecasts ({forecast_type.capitalize()})",
                "icon": "mdi:weather-partly-cloudy",
                "entity_id": entity_id,
                "type": forecast_type,
                "count": len(filtered_forecasts),
                "forecasts": filtered_forecasts,
            }
            
            # Update sensor
            # State = number of forecast items
            hass.states.async_set(
                sensor_id,
                len(filtered_forecasts),
                attributes
            )
            
            _LOGGER.info(f"Updated {sensor_id} with {len(filtered_forecasts)} forecast items")
            
        except Exception as e:
            _LOGGER.error(f"Error in get_forecasts service: {e}")
            
            # Set error state on failure
            sensor_id = FORECAST_DAILY_SENSOR if forecast_type == "daily" else FORECAST_HOURLY_SENSOR
            hass.states.async_set(
                sensor_id,
                "error",
                {
                    "friendly_name": f"Display Tools Forecasts ({forecast_type.capitalize()})",
                    "icon": "mdi:weather-off",
                    "error": str(e),
                }
            )
    
    # Register services
    hass.services.async_register(
        DOMAIN,
        "get_raw_translations",
        handle_get_raw_translations,
        schema=GET_RAW_TRANSLATIONS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    
    hass.services.async_register(
        DOMAIN,
        "get_translations",
        handle_get_translations,
        schema=GET_TRANSLATIONS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    
    hass.services.async_register(
        DOMAIN,
        "get_translations_esphome",
        handle_get_translations_esphome,
        schema=GET_TRANSLATIONS_ESPHOME_SCHEMA,
    )
    
    hass.services.async_register(
        DOMAIN,
        "save_media_cover",
        handle_save_media_cover,
        schema=SAVE_MEDIA_COVER_SCHEMA,
    )
    
    hass.services.async_register(
        DOMAIN,
        "get_forecasts",
        handle_get_forecasts,
        schema=GET_FORECASTS_SCHEMA,
    )
    
    _LOGGER.info("Display Tools integration setup completed")
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    
    # Unregister services
    hass.services.async_remove(DOMAIN, "get_raw_translations")
    hass.services.async_remove(DOMAIN, "get_translations")
    hass.services.async_remove(DOMAIN, "get_translations_esphome")
    hass.services.async_remove(DOMAIN, "save_media_cover")
    hass.services.async_remove(DOMAIN, "get_forecasts")
    
    # Remove sensor entities
    hass.states.async_remove(SENSOR_ENTITY_ID)
    hass.states.async_remove(FORECAST_DAILY_SENSOR)
    hass.states.async_remove(FORECAST_HOURLY_SENSOR)
    
    # Clean up hass.data
    hass.data.pop(DOMAIN, None)
    
    _LOGGER.info("Display Tools integration unloaded")
    
    return True


