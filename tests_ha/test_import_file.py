"""populate_past_usage: admin only, and it reads files only from allowed folders."""
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.atmos_energy.const import DOMAIN
from custom_components.atmos_energy.coordinator import AtmosEnergyDataUpdateCoordinator

XLS = b"daily usage spreadsheet"


@pytest.fixture
def folders(hass, tmp_path):
    """A config folder, a sibling whose name starts the same, and an allowlisted folder."""
    config, sibling, allowed = tmp_path / "config", tmp_path / "config_old", tmp_path / "media"
    for folder in (config, sibling, allowed):
        folder.mkdir()
        (folder / "usage.xls").write_bytes(XLS)
    hass.config.config_dir = str(config)
    hass.config.allowlist_external_dirs = {str(allowed)}
    return config, sibling, allowed


@pytest.fixture
def coordinator(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={"username": "u", "password": "p", "operation_mode": "daily"})
    entry.add_to_hass(hass)
    client = MagicMock()
    client._parse_xls_data = AsyncMock(return_value={"history": []})
    return AtmosEnergyDataUpdateCoordinator(hass, client, entry)


@pytest.mark.parametrize("path", ["usage.xls", "{config}/usage.xls", "{allowed}/usage.xls"])
async def test_reads_files_in_allowed_folders(coordinator, folders, path):
    config, _, allowed = folders
    await coordinator.async_import_historical_xls(path.format(config=config, allowed=allowed))
    coordinator.client._parse_xls_data.assert_awaited_once_with(XLS)


@pytest.mark.parametrize("path", ["{sibling}/usage.xls", "../config_old/usage.xls", "/etc/passwd"])
async def test_rejects_files_outside_allowed_folders(coordinator, folders, path):
    _, sibling, _ = folders
    with pytest.raises(ServiceValidationError):
        await coordinator.async_import_historical_xls(path.format(sibling=sibling))
    coordinator.client._parse_xls_data.assert_not_awaited()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
async def test_rejects_symlink_out_of_config(coordinator, folders):
    config, sibling, _ = folders
    os.symlink(sibling / "usage.xls", config / "link.xls")
    with pytest.raises(ServiceValidationError):
        await coordinator.async_import_historical_xls("link.xls")


async def test_missing_file_is_a_validation_error(coordinator, folders):
    with pytest.raises(ServiceValidationError, match="not found"):
        await coordinator.async_import_historical_xls("nope.xls")


async def test_populate_is_admin_only(hass, hass_read_only_user):
    assert await async_setup_component(hass, DOMAIN, {})
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN, "populate_past_usage", {"file_path": "usage.xls"},
            blocking=True, context=Context(user_id=hass_read_only_user.id),
        )


async def test_actions_exist_without_entries(hass):
    """Registered once in async_setup, so they don't depend on an entry loading."""
    assert await async_setup_component(hass, DOMAIN, {})
    assert hass.services.has_service(DOMAIN, "populate_past_usage")
    assert hass.services.has_service(DOMAIN, "refresh_current_usage")
    with pytest.raises(ServiceValidationError, match="No Atmos Energy account"):
        await hass.services.async_call(DOMAIN, "populate_past_usage", {"file_path": "usage.xls"}, blocking=True)
