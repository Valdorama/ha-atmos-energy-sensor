"""Published base rates (rates.json): validation and lookup.

rates.json ships inside the integration and is also fetched weekly from the repository's
master branch, so tariff changes reach every install without a new release. It holds what
changes about once a year: each region's customer charge and consumption rate (with the
date each took effect) and the Mid-Tex WNA parameters. The monthly gas cost is fetched
separately (gas_cost.py); taxes and riders vary by city and stay user-entered.

No Home Assistant imports: tests load this module directly.
"""
from __future__ import annotations

import json
import pathlib
from datetime import date
from typing import Any

SCHEMA = 1
BUNDLED_PATH = pathlib.Path(__file__).with_name("rates.json")

# Sanity ranges: a document with any value outside them is rejected whole
_LIMITS = {
    "fixed_cost": (0.0, 200.0),  # $/month
    "fixed_cost_summer": (0.0, 200.0),
    "usage_rate": (0.0, 5.0),  # $/Ccf
    "base_load": (0.0, 100.0),  # Ccf
    "heat_factor": (0.0, 2.0),  # Ccf/HDD
    "commodity_rate": (0.0, 1000.0),  # cents/Ccf
    "ndd": (0.0, 2000.0),  # degree days in a month
}


class InvalidRates(ValueError):
    """rates.json failed validation."""


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidRates(f"{field} is not a number")
    low, high = _LIMITS[field]
    if not low <= value <= high:
        raise InvalidRates(f"{field}={value} outside {low}-{high}")
    return float(value)


def _date(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as err:
        raise InvalidRates(f"{field} is not a YYYY-MM-DD date") from err


def validate(doc: Any) -> dict[str, Any]:
    """Return the document if it is a usable rates.json, else raise InvalidRates."""
    if not isinstance(doc, dict):
        raise InvalidRates("not an object")
    if doc.get("schema") != SCHEMA:
        raise InvalidRates(f"unsupported schema {doc.get('schema')!r}")
    _date(doc.get("updated"), "updated")

    regions = doc.get("regions")
    if not isinstance(regions, dict) or not regions:
        raise InvalidRates("no regions")
    for key, region in regions.items():
        rates = region.get("rates") if isinstance(region, dict) else None
        if not isinstance(rates, list) or not rates:
            raise InvalidRates(f"{key}: no rates")
        for entry in rates:
            if not isinstance(entry, dict):
                raise InvalidRates(f"{key}: rate entry is not an object")
            _date(entry.get("effective"), f"{key}.effective")
            _number(entry.get("fixed_cost"), "fixed_cost")
            _number(entry.get("usage_rate"), "usage_rate")
            if entry.get("fixed_cost_summer") is not None:
                _number(entry["fixed_cost_summer"], "fixed_cost_summer")

    for key, wna in (doc.get("wna") or {}).items():
        if not isinstance(wna, dict) or not isinstance(wna.get("stations"), dict):
            raise InvalidRates(f"wna.{key}: no stations")
        _number(wna.get("commodity_rate"), "commodity_rate")
        for station, params in wna["stations"].items():
            if not isinstance(params, dict) or not isinstance(params.get("ndd"), dict):
                raise InvalidRates(f"wna.{key}.{station}: bad station")
            _number(params.get("base_load"), "base_load")
            _number(params.get("heat_factor"), "heat_factor")
            for month, value in params["ndd"].items():
                if month not in {str(m) for m in range(1, 13)}:
                    raise InvalidRates(f"wna.{key}.{station}: bad month {month!r}")
                _number(value, "ndd")
    return doc


def load_bundled() -> dict[str, Any]:
    """The copy shipped with this version (blocking file read; run in an executor)."""
    return validate(json.loads(BUNDLED_PATH.read_text(encoding="utf-8")))


def newer(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any] | None:
    """Whichever document has the later "updated" date (a wins a tie)."""
    if a is None or b is None:
        return a or b
    return b if date.fromisoformat(b["updated"]) > date.fromisoformat(a["updated"]) else a


def base_rates(doc: dict[str, Any] | None, region: str, day: date) -> dict[str, Any] | None:
    """The region's rate entry in effect on `day`.

    Days before the earliest entry get the earliest one: closer than nothing.
    """
    rates = ((doc or {}).get("regions") or {}).get(region, {}).get("rates") or []
    if not rates:
        return None
    ordered = sorted(rates, key=lambda e: e["effective"])
    in_effect = [e for e in ordered if date.fromisoformat(e["effective"]) <= day]
    return in_effect[-1] if in_effect else ordered[0]


def wna_params(doc: dict[str, Any] | None, region: str, station: str) -> dict[str, Any] | None:
    """WNA inputs for one weather station: base_load, heat_factor, commodity_rate, ndd {month: dd}."""
    wna = ((doc or {}).get("wna") or {}).get(region)
    params = (wna or {}).get("stations", {}).get(station)
    if not params:
        return None
    return {
        "base_load": params["base_load"],
        "heat_factor": params["heat_factor"],
        "commodity_rate": wna["commodity_rate"],
        "ndd": {int(m): v for m, v in params["ndd"].items()},
    }
