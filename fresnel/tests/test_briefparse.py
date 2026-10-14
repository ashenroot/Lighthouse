from fresnel import briefparse
from fresnel.push import validate_payload

BRIEF = """Daily Preparedness Briefing - October 14, 2026

CORE TIER
1. Geopolitical / Conflict [NOTABLE]
Talks continue.
  1. a numbered point inside the section
  2. another one

2. Domestic Security [quiet]
Nothing new.

3. Critical Infrastructure & Cyber [CRITICAL]
CISA issued an emergency directive.
https://www.cisa.gov/example

WATCH TIER
4. Financial
No change.

Sources
https://www.cisa.gov/example
https://www.nhc.noaa.gov/gtwo.php
"""


def test_split_sections_levels_and_groups():
    intro, sections = briefparse.split(BRIEF)
    assert intro.startswith("Daily Preparedness Briefing")
    assert [s["title"] for s in sections] == ["Geopolitical / Conflict", "Domestic Security", "Critical Infrastructure & Cyber", "Financial", "Sources"]
    assert [s["criticality"] for s in sections] == ["notable", "quiet", "critical", None, None]
    assert [s["group"] for s in sections] == ["Core tier", "Core tier", "Core tier", "Watch tier", None]
    assert "numbered point inside" in sections[0]["body"]  # numbered list items stay in their section
    assert sections[-1]["sources"] == ["https://www.cisa.gov/example", "https://www.nhc.noaa.gov/gtwo.php"]


def test_level_aliases():
    assert [briefparse.level(x) for x in ("HIGH", "Severe", "low", "bogus", None)] == ["elevated", "critical", "quiet", None, None]


def test_unstructured_text_is_left_alone():
    assert briefparse.split("Just a paragraph.\n\n1. one item\n2. another") is None


def test_validate_payload_splits_text_brief():
    data = validate_payload("brief", {"generated_at": "2026-10-14T12:00:00Z", "format": "text", "title": "t", "body": BRIEF})
    assert len(data["sections"]) == 5 and data["body"].startswith("Daily Preparedness")
    assert data["sections"][2]["criticality"] == "critical"


def test_explicit_sections_are_not_reparsed():
    sec = [{"title": "Mine", "body": BRIEF, "criticality": "elevated"}]
    data = validate_payload("brief", {"generated_at": "2026-10-14T12:00:00Z", "body": "intro", "sections": sec})
    assert len(data["sections"]) == 1 and data["body"] == "intro"
