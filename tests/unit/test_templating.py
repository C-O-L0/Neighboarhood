from datetime import date
from decimal import Decimal

from app.repos.dto import RecentSale
from app.templating import lease, money, month, pct, storey, templates, unknown


def test_money():
    assert money(Decimal("1234.5")) == "$1,234.50"
    assert money(Decimal("-12500")) == "-$12,500"
    assert money(Decimal("468000.00")) == "$468,000"
    assert money(Decimal("0.004")) == "$0"
    assert money(Decimal("612345.675")) == "$612,345.68"
    assert money(None) == "Unknown"


def test_month():
    assert month(date(2024, 1, 1)) == "Jan 2024"


def test_lease_uses_the_sources_wording():
    assert lease(733) == "61 years 01 month"
    assert lease(732) == "61 years"
    assert lease(735) == "61 years 03 months"
    assert lease(12) == "1 year"
    assert lease(13) == "1 year 01 month"


def test_storey():
    assert storey(7, 9) == "07 TO 09"


def test_pct():
    assert pct(Decimal("-3.4")) == "-3.4%"
    assert pct(None) == ""


def test_unknown():
    assert unknown(None) == "Unknown"
    assert unknown(0) == 0
    assert unknown("x") == "x"


def test_filters_are_registered_in_the_environment():
    for name in ("money", "month", "lease", "storey", "pct", "unknown"):
        assert name in templates.env.filters


def test_price_psm_rounds_half_up_in_python():
    sale = RecentSale(1, date(2024, 1, 1), "4 ROOM", "Improved", 1, 3, Decimal("90.0"), Decimal("450001.00"))
    assert sale.price_psm == Decimal("5000.01")
