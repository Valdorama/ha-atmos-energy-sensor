"""Atmos Energy rate regions: where each one publishes its monthly gas cost, and setup defaults.

Pure data with no Home Assistant imports, so the gas-cost parser tests can load it directly.

Each region's gas cost (called GCR, GCA or PGA depending on the state) is published monthly as a
PDF table of recent months. The PDF is linked from the region's tariff page on atmosenergy.com,
and the parser finds the residential value with:
- page: which PDF page holds the table (Colorado has one page per area)
- column: regex for the header word above the residential column
- occurrence: which match of that header to use, counting top-to-bottom then left-to-right,
  for PDFs with several tables on one page (firm customers first)

Defaults are only starting values for the setup form. Users should check them against a bill.
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_REGION = "mid_tex"
WNA_REGION = "mid_tex"  # The weather normalization model (advanced mode) is Mid-Tex only
SUMMER_MONTHS = range(5, 10)  # May-September, for regions with a seasonal customer charge


@dataclass(frozen=True)
class GasCostSource:
    """Where a region's monthly gas-cost PDF lives and how to read it."""

    tariff_page: str  # path under https://www.atmosenergy.com/
    document_prefix: str  # slug before "-{month}-{year}", e.g. "mid-tex-gcr-rates"
    column: str
    page: int = 0
    occurrence: int = 0


@dataclass(frozen=True)
class Region:
    """One Atmos rate region, or one area of a region whose gas cost differs by area."""

    name: str
    gas_cost: GasCostSource
    fixed_cost: float  # monthly customer charge, $
    usage_rate: float  # distribution / consumption charge, $/Ccf
    gas_cost_rate: float  # fallback gas cost, $/Ccf (October 2026 value)
    other_charges: float = 0.0  # per-Ccf riders and surcharges, $/Ccf
    tax_percent: float = 0.0
    fixed_cost_summer: float | None = None  # May-September customer charge, if different


def _source(state_page: str, prefix: str, column: str, **kwargs) -> GasCostSource:
    return GasCostSource(f"utility-operationsrates/{state_page}", prefix, column, **kwargs)


REGIONS: dict[str, Region] = {
    "mid_tex": Region(
        name="Texas: Mid-Tex",
        gas_cost=_source("tariffs-mid-tex", "mid-tex-gcr-rates", r"^Residential$"),
        fixed_cost=24.40,
        usage_rate=1.03839,
        gas_cost_rate=1.09025,
        other_charges=0.01843,  # Winter Storm Uri surcharge
        tax_percent=8.08,
    ),
    "west_texas": Region(
        name="Texas: West Texas",
        gas_cost=_source("tariffs-west-texas/west-texas", "west-texas-gca-rates", r"^GCA$"),
        fixed_cost=16.75,
        usage_rate=0.72220,  # Amarillo, Lubbock and unincorporated areas; other cities 17.45 / 0.68910
        gas_cost_rate=0.489,  # includes the Customer Rate Relief (Uri) charge
    ),
    "louisiana": Region(
        name="Louisiana",
        gas_cost=_source("tariffs-louisiana", "louisiana-pga-rates", r"^BCC,$"),
        fixed_cost=31.25,
        usage_rate=0.72769,
        gas_cost_rate=0.50254,
    ),
    "mississippi": Region(
        name="Mississippi",
        # "Combined Year Round" applies to the year-round residential schedules
        gas_cost=_source("tariffs-mississippi", "mississippi-pga-rates", r"^Round$"),
        fixed_cost=30.50,
        usage_rate=0.70181,  # interim rates, subject to refund
        gas_cost_rate=0.67500,
    ),
    "kansas": Region(
        name="Kansas",
        gas_cost=_source("tariffs-kansas", "kansas-pga-rates", r"^Firm$"),
        fixed_cost=23.00,
        usage_rate=0.24290,
        gas_cost_rate=0.49923,
        other_charges=0.0055,
    ),
    "kentucky": Region(
        name="Kentucky",
        gas_cost=_source("tariffs-kentucky", "kentucky-gca-rates", r"^G-1$"),
        fixed_cost=25.00,
        usage_rate=0.16261,  # $1.6261/Mcf
        gas_cost_rate=0.55595,
        other_charges=0.00163,
    ),
    "virginia": Region(
        name="Virginia",
        gas_cost=_source("tariffs-virginia", "virginia-pga-rates", r"^TOTAL$"),
        fixed_cost=13.24,
        usage_rate=0.2404,
        gas_cost_rate=0.5820,
    ),
    "colorado_north": Region(
        name="Colorado: North",
        gas_cost=_source("tariffs-colorado/colorado", "colorado-gca-rates", r"^GCA$", page=0),
        fixed_cost=11.60,
        usage_rate=0.43030,
        gas_cost_rate=0.58151,
        other_charges=0.00085,
    ),
    "colorado_southeast": Region(
        name="Colorado: Southeast",
        gas_cost=_source("tariffs-colorado/colorado", "colorado-gca-rates", r"^GCA$", page=1),
        fixed_cost=11.60,
        usage_rate=0.43030,
        gas_cost_rate=0.37000,
        other_charges=0.00085,
    ),
    "colorado_southwest": Region(
        name="Colorado: Southwest",
        gas_cost=_source("tariffs-colorado/colorado", "colorado-gca-rates", r"^GCA$", page=2),
        fixed_cost=11.60,
        usage_rate=0.43030,
        gas_cost_rate=0.40000,
        other_charges=0.00085,
    ),
    "tennessee": Region(
        name="Tennessee (except Union City)",
        gas_cost=_source("tariffs-tennessee", "tennessee-pga-rates", r"^TOTAL$"),
        fixed_cost=27.20,
        fixed_cost_summer=25.20,
        usage_rate=0.1970,
        gas_cost_rate=0.5368,
    ),
    "tennessee_union_city": Region(
        name="Tennessee: Union City",
        gas_cost=_source("tariffs-tennessee", "union-city-pga-rates", r"^TOTAL$"),
        fixed_cost=27.20,
        fixed_cost_summer=25.20,
        usage_rate=0.1970,
        gas_cost_rate=0.3102,
    ),
}


def get_region(key: str | None) -> Region:
    """The region for a config entry; entries made before regions existed are Mid-Tex."""
    return REGIONS.get(key or DEFAULT_REGION, REGIONS[DEFAULT_REGION])
