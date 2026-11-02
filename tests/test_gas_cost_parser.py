"""Gas-cost PDF parsing for every region, against the October 2026 PDFs in tests/fixtures/gas_cost.

Loads regions.py and gas_cost_parser.py by path, so it runs without Home Assistant installed.
"""
import importlib.util
import pathlib
import sys

import pytest

pytest.importorskip("pdfplumber")

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "custom_components" / "atmos_energy"
FIXTURES = ROOT / "tests" / "fixtures" / "gas_cost"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"atmos_{name}", PACKAGE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


regions = _load("regions")
parser = _load("gas_cost_parser")

# region: (fixture, months of history, Feb 2026 rate, Oct 2026 rate)
EXPECTED = {
    "mid_tex": ("mid-tex", 13, 1.16939, 1.09025),
    "west_texas": ("west-texas", 13, 0.389, 0.489),
    "louisiana": ("louisiana", 25, 0.85278, 0.50254),
    "mississippi": ("mississippi", 13, 0.675, 0.675),
    "kansas": ("kansas", 25, 0.71869, 0.49923),  # Firm, not the Irrigation column (0.78708)
    "kentucky": ("kentucky", 25, 0.50042, 0.55595),
    "virginia": ("virginia", 25, 0.7691, 0.5820),  # firm total, after the negative ACA
    "colorado_north": ("colorado", 13, 0.64811, 0.58151),
    "colorado_southeast": ("colorado", 13, 0.44861, 0.37),
    "colorado_southwest": ("colorado", 13, 0.39, 0.40),
    "tennessee": ("tennessee", 25, 0.7177, 0.5368),  # firm table, oldest month first
    "tennessee_union_city": ("union-city", 25, 0.5331, 0.3102),
}


def test_every_region_has_a_fixture_expectation():
    assert set(EXPECTED) == set(regions.REGIONS)


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_parses_region_history(key):
    fixture, months, feb, oct_ = EXPECTED[key]
    source = regions.REGIONS[key].gas_cost
    content = (FIXTURES / f"{fixture}-2026-10.pdf").read_bytes()

    rates = parser.parse_gas_cost_pdf(content, source.column, source.page, source.occurrence)

    assert len(rates) == months
    assert rates[(2026, 2)] == pytest.approx(feb)
    assert rates[(2026, 10)] == pytest.approx(oct_)
    assert max(rates) == (2026, 10)


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_fallback_default_matches_latest_pdf(key):
    assert regions.REGIONS[key].gas_cost_rate == pytest.approx(EXPECTED[key][3])


@pytest.mark.parametrize("strings_file", ["strings.json", "translations/en.json"])
def test_every_region_has_a_label(strings_file):
    import json

    strings = json.loads((PACKAGE / strings_file).read_text(encoding="utf-8"))
    assert set(strings["selector"]["region"]["options"]) == set(regions.REGIONS)


def test_missing_column_returns_nothing():
    content = (FIXTURES / "kansas-2026-10.pdf").read_bytes()
    assert parser.parse_gas_cost_pdf(content, r"^Residential$") == {}


def test_page_out_of_range_returns_nothing():
    content = (FIXTURES / "kansas-2026-10.pdf").read_bytes()
    assert parser.parse_gas_cost_pdf(content, r"^Firm$", page=3) == {}


@pytest.mark.parametrize(
    "text, expected",
    [("Oct-26", (2026, 10)), ("October-24", (2024, 10)), ("Sept-25", (2025, 9)), ("Oct-2026", (2026, 10)),
     ("Month", None), ("Foo-26", None)],
)
def test_parse_month_label(text, expected):
    assert parser.parse_month_label(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [("0.58151", 0.58151), ("$0.50254", 0.50254), ("-0.00820", -0.0082), ("(0.0206)", -0.0206),
     (".1274", 0.1274), ("G-1", None), ("26", None)],
)
def test_parse_number(text, expected):
    assert parser.parse_number(text) == (pytest.approx(expected) if expected is not None else None)


def test_split_numbers_are_joined():
    words = [
        {"text": "Oct-26", "x0": 10, "x1": 40, "top": 100},
        {"text": "0", "x0": 60, "x1": 63, "top": 100},
        {"text": ".1274", "x0": 63, "x1": 75, "top": 100},
        {"text": "Rate", "x0": 55, "x1": 80, "top": 90},
    ]
    assert parser.extract_rates(words, r"^Rate$") == {(2026, 10): pytest.approx(0.1274)}
