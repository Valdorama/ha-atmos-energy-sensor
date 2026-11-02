"""rates.json (published base rates): the bundled copy, validation and lookups.

Loads modules by path, so it runs without Home Assistant installed.
"""
import copy
import importlib.util
import pathlib
import sys
from datetime import date

import pytest

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "atmos_energy"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(f"atmos_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rates_data = _load("rates_data", PACKAGE / "rates_data.py")
regions = _load("regions", PACKAGE / "regions.py")
ndd_data = _load("ndd_data", PACKAGE / "wna" / "ndd_data.py")

BUNDLED = rates_data.load_bundled()
TODAY = date(2026, 11, 2)


def test_bundled_covers_every_region():
    assert set(BUNDLED["regions"]) == set(regions.REGIONS)


@pytest.mark.parametrize("key", sorted(regions.REGIONS))
def test_bundled_matches_setup_defaults(key):
    """regions.py defaults and the latest bundled entry must not drift apart."""
    region = regions.REGIONS[key]
    latest = rates_data.base_rates(BUNDLED, key, TODAY)
    assert latest["fixed_cost"] == pytest.approx(region.fixed_cost)
    assert latest["usage_rate"] == pytest.approx(region.usage_rate)
    assert latest.get("fixed_cost_summer") == region.fixed_cost_summer


@pytest.mark.parametrize("station", sorted(ndd_data.WEATHER_STATION_CONFIG))
def test_bundled_wna_matches_built_in(station):
    params = rates_data.wna_params(BUNDLED, "mid_tex", station)
    built_in = ndd_data.WEATHER_STATION_CONFIG[station]
    assert params["base_load"] == built_in["base_load_residential"]
    assert params["heat_factor"] == built_in["heat_factor_residential"]
    assert params["commodity_rate"] == built_in["commodity_rate"]
    assert params["ndd"] == ndd_data.MID_TEX_NDD[station]


@pytest.mark.parametrize(
    "day, fixed, usage",
    [
        (date(2026, 9, 30), 25.03, 0.78025),  # before the October 2026 rate review
        (date(2026, 10, 1), 24.40, 1.03839),
        (date(2024, 1, 1), 25.03, 0.78025),  # before the first entry: earliest one
    ],
)
def test_base_rates_by_effective_date(day, fixed, usage):
    entry = rates_data.base_rates(BUNDLED, "mid_tex", day)
    assert entry["fixed_cost"] == pytest.approx(fixed)
    assert entry["usage_rate"] == pytest.approx(usage)


def test_unknown_region_or_station():
    assert rates_data.base_rates(BUNDLED, "nowhere", TODAY) is None
    assert rates_data.base_rates(None, "mid_tex", TODAY) is None
    assert rates_data.wna_params(BUNDLED, "mid_tex", "nowhere") is None
    assert rates_data.wna_params(BUNDLED, "kansas", "austin") is None


def _broken(mutate):
    doc = copy.deepcopy(BUNDLED)
    mutate(doc)
    return doc


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(schema=2),
        lambda d: d.update(updated="last week"),
        lambda d: d.update(regions={}),
        lambda d: d["regions"]["kansas"]["rates"][0].update(usage_rate=24.29),  # $/Mcf typed as $/Ccf
        lambda d: d["regions"]["kansas"]["rates"][0].update(fixed_cost="23"),
        lambda d: d["regions"]["kansas"]["rates"][0].update(fixed_cost=True),
        lambda d: d["regions"]["kansas"]["rates"][0].update(effective="2026-13-01"),
        lambda d: d["regions"]["kansas"]["rates"][0].pop("fixed_cost"),
        lambda d: d["wna"]["mid_tex"]["stations"]["austin"]["ndd"].update({"13": 100}),
        lambda d: d["wna"]["mid_tex"]["stations"]["austin"].update(heat_factor=-0.1),
    ],
)
def test_validation_rejects(mutate):
    with pytest.raises(rates_data.InvalidRates):
        rates_data.validate(_broken(mutate))


def test_validation_rejects_non_object():
    with pytest.raises(rates_data.InvalidRates):
        rates_data.validate([])


def test_newer_prefers_later_update():
    later = _broken(lambda d: d.update(updated="2027-01-15"))
    assert rates_data.newer(BUNDLED, later) is later
    assert rates_data.newer(later, BUNDLED) is later
    assert rates_data.newer(BUNDLED, copy.deepcopy(BUNDLED)) is BUNDLED  # tie: first wins
    assert rates_data.newer(None, BUNDLED) is BUNDLED
