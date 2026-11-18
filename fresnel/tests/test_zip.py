"""Location from a ZIP Code."""
import pytest
from pydantic import ValidationError

from fresnel import zipcodes
from fresnel.config import Location


def test_lookup_finds_place_county_and_state():
    z = zipcodes.lookup("64111")
    assert z["state"] == "MO" and z["county_fips"] == "29095" and z["county_name"] == "Jackson County"
    assert 39.0 < z["lat"] < 39.2 and -94.7 < z["lon"] < -94.5


def test_lookup_accepts_ints_plus4_and_a_lost_leading_zero():
    assert zipcodes.normalize(30213) == "30213"
    assert zipcodes.normalize("30213-4567") == "30213"
    assert zipcodes.normalize(2134) == "02134"
    assert zipcodes.lookup("02134")["state"] == "MA"
    assert zipcodes.lookup("00901")["state"] == "PR"


@pytest.mark.parametrize("bad", ["", "abcde", "123456", "00000", "99999", None])
def test_lookup_rejects_what_is_not_a_zip(bad):
    assert zipcodes.lookup(bad) is None


def test_location_fills_everything_from_a_zip():
    loc = Location(zip="64111")
    assert (loc.state, loc.county_fips, loc.name) == ("MO", "29095", "Jackson County, MO")
    assert loc.zip == "64111" and loc.state_fips == "29" and loc.county_code == "095"


def test_explicit_values_win_over_the_zip():
    loc = Location(zip="64111", name="The farm", lat=38.9, lon=-94.4, county_fips="29037")
    assert loc.name == "The farm" and loc.lat == 38.9 and loc.lon == -94.4 and loc.county_fips == "29037" and loc.state == "MO"


def test_unknown_zip_is_a_clear_error():
    with pytest.raises(ValidationError, match="not a known US ZIP"):
        Location(zip="99999")


def test_unquoted_short_zip_asks_for_quotes():
    with pytest.raises(ValidationError, match="in quotes"):
        Location(zip=2134)


def test_without_a_zip_the_old_fields_are_still_required():
    with pytest.raises(ValidationError):
        Location(lat=39.1, lon=-94.6)
    assert Location(lat=39.1, lon=-94.6, state="MO", county_fips="29095").zip is None


def test_the_published_location_never_carries_the_zip():
    pub = Location(zip="64111").public()
    assert "zip" not in pub and "64111" not in str(pub)


def test_the_zip_table_ships_with_the_repository():
    """The table once sat in a folder the repository ignores (data/), so a fresh checkout had none and every ZIP lookup failed."""
    import shutil
    import subprocess
    from pathlib import Path

    table = Path(zipcodes.__file__).parent / "zipdata" / "zcta.tsv.gz"
    assert table.is_file()
    if shutil.which("git") and (Path(__file__).resolve().parents[2] / ".git").exists():
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", str(table)], cwd=table.parent, capture_output=True)
        ignored = subprocess.run(["git", "check-ignore", "-q", str(table)], cwd=table.parent)
        assert ignored.returncode == 1, "the ZIP table is matched by .gitignore"
        assert tracked.returncode == 0, "the ZIP table is not committed"
    assert len(zipcodes._table()) > 33000
