"""Life360 Device Tracker platform."""

from __future__ import annotations

import asyncio
from typing import Any, cast, Mapping

from homeassistant.components.device_tracker import SOURCE_TYPE_GPS
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_BATTERY_CHARGING,
    ATTR_BATTERY_LEVEL,
    ATTR_ENTITY_PICTURE,
    ATTR_GPS_ACCURACY,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_NAME,
    CONF_PREFIX,
)
from homeassistant.core import callback, HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback, EntityPlatform
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import (
    ATTR_ADDRESS,
    ATTR_AT_LOC_SINCE,
    ATTR_DRIVING,
    ATTR_LAST_SEEN,
    ATTR_PLACE,
    ATTR_SPEED,
    ATTR_WIFI_ON,
    ATTRIBUTION,
    CONF_DRIVING_SPEED,
    CONF_MAX_GPS_ACCURACY,
    DOMAIN,
    LOGGER,
)


_EXTRA_ATTRIBUTES = (
    ATTR_ADDRESS,
    ATTR_AT_LOC_SINCE,
    ATTR_BATTERY_CHARGING,
    ATTR_DRIVING,
    ATTR_LAST_SEEN,
    ATTR_PLACE,
    ATTR_SPEED,
    ATTR_WIFI_ON,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the device tracker platform."""
    # LOGGER.debug("device_tracker.async_setup_entry called: %s", entry.as_dict())
    account = hass.data[DOMAIN]["accounts"][entry.unique_id]
    coordinator = account["coordinator"]
    tracked_members = hass.data[DOMAIN]["tracked_members"]
    included_circles_members = set()
    logged_circles = []
    logged_places = []
    logged_members = []

    def _include_name(filter: dict[str, bool | list[str]], name: str) -> bool:
        return True

    @callback
    def process_data() -> None:
        for circle_id, circle in coordinator.data["circles"].items():
            circle_name = circle["name"]
            circle_desc = f"Circle {circle_name} from account {entry.unique_id}"
            incl_circle = _include_name({}, circle_name)
            if circle_id not in logged_circles:
                logged_circles.append(circle_id)
                LOGGER.info(
                    "%s: will%s be included",
                    circle_desc,
                    "" if incl_circle else " NOT",
                )

            if not incl_circle:
                continue

            included_circles_members.update(circle["members"])

            new_places = []
            for place_id, place in circle["places"].items():
                if place_id in logged_places:
                    continue
                logged_places.append(place_id)
                new_places.append(place)
            if new_places:
                msg = f"{circle_desc}: Places:"
                for place in new_places:
                    msg += f"\n- name: {place['name']}"
                    msg += f"\n  latitude: {place['latitude']}"
                    msg += f"\n  longitude: {place['longitude']}"
                    msg += f"\n  radius: {place['radius']}"
                LOGGER.debug(msg)

        new_entities = []
        for member_id, member in coordinator.data["members"].items():
            member_name = member[ATTR_NAME]
            incl_member = (
                member_id in included_circles_members
                and member_id not in tracked_members
                and _include_name({}, member_name)
            )
            if member_id not in logged_members:
                logged_members.append(member_id)
                LOGGER.info(
                    "%s: will%s be tracked via account %s",
                    member_name,
                    "" if incl_member else " NOT",
                    entry.unique_id,
                )

            if not incl_member:
                continue

            tracked_members.append(member_id)
            new_entities.append(Life360DeviceTracker(coordinator, member_id))
        async_add_entities(new_entities)

    process_data()
    account["unsub"] = coordinator.async_add_listener(process_data)


class Life360DeviceTracker(CoordinatorEntity, TrackerEntity):
    """Life360 Device Tracker."""

    def __init__(self, coordinator: DataUpdateCoordinator, member_id: str) -> None:
        super().__init__(coordinator)
        self._attr_attribution = ATTRIBUTION
        self._attr_unique_id = member_id
        self._data = coordinator.data["members"][self.unique_id]

        self._attr_name = self._data[ATTR_NAME]
        self._attr_entity_picture = self._data[ATTR_ENTITY_PICTURE]

    @callback
    def add_to_platform_start(
        self,
        hass: HomeAssistant,
        platform: EntityPlatform,
        parallel_updates: asyncio.Semaphore | None,
    ) -> None:
        """Start adding an entity to a platform."""
        platform.entity_namespace = self.coordinator.config_entry.options.get(
            CONF_PREFIX
        )
        super().add_to_platform_start(hass, platform, parallel_updates)

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity will be removed from hass."""
        await super().async_will_remove_from_hass()
        self.hass.data[DOMAIN]["tracked_members"].remove(self.unique_id)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Get a shortcut to this member's data. Can't guarantee it's the same dict every
        # update, or that there is even data for this member every update, so need to
        # update shortcut each time.
        self._data = self.coordinator.data["members"].get(self.unique_id)
        # Skip update if max GPS accuracy exceeded.
        max_gps_acc = self.coordinator.config_entry.options.get(CONF_MAX_GPS_ACCURACY)
        if max_gps_acc is not None and self.location_accuracy > max_gps_acc:
            LOGGER.warning(
                "%s: Ignoring update because expected GPS accuracy (%i) is not met: %i",
                self.entity_id,
                max_gps_acc,
                self.location_accuracy,
            )
            return
        super()._handle_coordinator_update()

    @property
    def force_update(self) -> bool:
        """Return True if state updates should be forced."""
        return False

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Guard against member not being in last update for some reason.
        return super().available and self._data is not None

    @property
    def entity_picture(self) -> str | None:
        """Return the entity picture to use in the frontend, if any."""
        if self.available:
            self._attr_entity_picture = self._data[ATTR_ENTITY_PICTURE]
        return super().entity_picture

    # All of the following will only be called if self.available.

    @property
    def battery_level(self) -> int | None:
        """Return the battery level of the device.
        Percentage from 0-100.
        """
        return cast(int, self._data[ATTR_BATTERY_LEVEL])

    @property
    def source_type(self) -> str:
        """Return the source type, eg gps or router, of the device."""
        return SOURCE_TYPE_GPS

    @property
    def location_accuracy(self) -> int:
        """Return the location accuracy of the device.

        Value in meters.
        """
        return cast(int, self._data[ATTR_GPS_ACCURACY])

    @property
    def latitude(self) -> float | None:
        """Return latitude value of the device."""
        return cast(float, self._data[ATTR_LATITUDE])

    @property
    def longitude(self) -> float | None:
        """Return longitude value of the device."""
        return cast(float, self._data[ATTR_LONGITUDE])

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return entity specific state attributes."""
        attrs = {
            k: v
            for k, v in self._data.items()
            if k in _EXTRA_ATTRIBUTES and v is not None
        }
        driving_speed = self.coordinator.config_entry.options.get(CONF_DRIVING_SPEED)
        if driving_speed is not None:
            attrs[ATTR_DRIVING] = attrs[ATTR_DRIVING] or (
                attrs[ATTR_SPEED] > driving_speed
            )
        return attrs
