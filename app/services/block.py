"""The block profile (UC4) and its lease check (UC6): the summary sentence, the headline flat type and lease left.

Only the lease start year is known, so lease left is in whole years: 99 years from the lease start year, less the
years gone by. "Now" is the year of the latest registration month in the data (USE_CASES R4), as on Explore, so
the profile and the Explore block card agree. The lease check takes a future year, or the buyer's age now and a
target age, and an optional least lease left to warn below. CPF and loan rules are not built in (UC6 note): the
page links to the official CPF page instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.repos.dto import BlockFoodAccess, BuildingDetail, FlatTypeRecent
from app.services.explore import LEASE_YEARS

CPF_LEASE_URL = "https://www.cpf.gov.sg/member/home-ownership/using-your-cpf-to-buy-a-home"
MAX_AHEAD = 120  # years after now a lease check may look
MAX_AGE = 120
TEXT_MAX = 4  # the longest input any lease check field takes ("2040", "55")
FIELDS = ("at", "age", "at_age", "min_left")


def lease_left(lease_year: int, year: int) -> int:
    """Whole years of lease left in `year`; 0 or less means the lease has run out."""
    return lease_year + LEASE_YEARS - year


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def summary(b: BuildingDetail, food: BlockFoodAccess | None, now: int) -> str:
    """One sentence on what matters first (USE_CASES R7): age, lease left and food nearby."""
    parts = []
    if b.year_completed is not None:
        parts.append(f"Completed {b.year_completed}")
    if b.lease_commence_year is not None:
        left = lease_left(b.lease_commence_year, now)
        parts.append(f"about {plural(left, 'year')} of lease left" if left > 0 else "lease has run out")
    if food is not None:
        parts.append(f"{plural(food.within_1km, 'open hawker centre')} within 1 km")
    else:
        parts.append("location not available, so no food access figures")
    text = ", ".join(parts)
    return text[:1].upper() + text[1:] + "."


def headline(recent: list[FlatTypeRecent]) -> FlatTypeRecent | None:
    """The flat type sold most in the window (ties: the first in flat type order), whose tags lead the page."""
    return max(recent, key=lambda r: r.n, default=None)  # max keeps the first of equal counts


@dataclass(frozen=True)
class LeaseCheck:
    """A validated lease check. year is the year looked at; age is the buyer's age then, when given."""

    year: int
    left: int
    age: int | None = None
    min_left: int | None = None

    @property
    def expired(self) -> bool:
        return self.left <= 0

    @property
    def below(self) -> bool:
        return self.min_left is not None and self.left < self.min_left


def _whole(text: str, lo: int, hi: int) -> int | None:
    text = text.strip()
    if not (text.isascii() and text.isdigit() and len(text) <= TEXT_MAX):
        return None
    n = int(text)
    return n if lo <= n <= hi else None


def lease_check(values: dict[str, str], lease_year: int, now: int) -> tuple[LeaseCheck | None, list[str]]:
    """The lease check for the given query values, and error messages. (None, []) when nothing was asked."""
    given = {k: values.get(k, "").strip() for k in FIELDS}
    if not any(given.values()):
        return None, []
    errors = []
    min_left = None
    if given["min_left"]:
        min_left = _whole(given["min_left"], 1, LEASE_YEARS - 1)
        if min_left is None:
            errors.append(f"Least lease left must be a whole number of years from 1 to {LEASE_YEARS - 1}.")
    year = age = None
    if given["age"] or given["at_age"]:
        now_age = _whole(given["age"], 1, MAX_AGE) if given["age"] else None
        age = _whole(given["at_age"], 1, MAX_AGE) if given["at_age"] else None
        if now_age is None or age is None:
            errors.append(f"Enter both ages as whole numbers from 1 to {MAX_AGE}.")
        elif age <= now_age:
            errors.append("The age to check must be older than your age now.")
        else:
            year = now + age - now_age
    elif given["at"]:
        year = _whole(given["at"], now, now + MAX_AHEAD)
        if year is None:
            errors.append(f"Year must be from {now} to {now + MAX_AHEAD}.")
    else:
        errors.append("Enter a year, or your age now and the age to check.")
    if errors or year is None:
        return None, errors
    return LeaseCheck(year, lease_left(lease_year, year), age, min_left), []
