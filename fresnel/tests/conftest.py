import json
import re
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from fresnel.app import create_app
from fresnel.config import Config
from fresnel.relay import Relay

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.t = NOW

    def __call__(self):
        return self.t


DOE_PAGE = (
    "<html><body>"
    "<p>On October 12, 2026 , the U.S. Department of Energy (DOE) issued emergency DOE Order No. 202-26-50 , pursuant to section 202(c) "
    "of the Federal Power Act, to PJM Interconnection, L.L.C. (PJM) . The order directs PJM to dispatch specified units and to order their "
    "operation as needed to maintain reliability. The order is effective through January 10, 2027.</p>"
    "<p>On October 10, 2026 , the U.S. Department of Energy (DOE) issued DOE Order No. 202-26-49 , pursuant to section 202(c), "
    "to Tri-State Generation and Transmission (Tri-State) . Unit 1 remains available to operate. The order is in effect through January 8, 2027.</p>"
    "<p>On July 1, 2026 , the U.S. Department of Energy (DOE) issued DOE Order No. 202-26-30 , pursuant to section 202(c), "
    "to Duke Energy Carolinas, LLC (DEC) . The order directs DEC to dispatch specified units. The order is effective through September 29, 2026.</p>"
    "</body></html>"
)

ODIN_STATES = [{"state": "Kansas", "out": 9210, "n": 31}, {"state": "Missouri", "out": 1500, "n": 6},
               {"state": "Texas", "out": 1055, "n": 51}, {"state": "Utah", "out": None, "n": 1}]
ODIN_MO = [{"communitydescriptor": "29095", "county": "Jackson", "out": 1200, "n": 4},
           {"communitydescriptor": "29047", "county": "Clay", "out": 300, "n": 2}]


def odin_feed(request: httpx.Request) -> dict:
    q = request.url.params
    if q.get("group_by") == "state":
        return {"total_count": len(ODIN_STATES), "results": [dict(r) for r in ODIN_STATES]}
    assert q.get("where") == 'startswith(communitydescriptor,"29")'
    return {"total_count": len(ODIN_MO), "results": [dict(r) for r in ODIN_MO]}


def _gauge(lid, name, lat, lon, obs, fc="fcst_not_current", age_min=30, stage=4.0, fc_stage=-999):
    when = (NOW - timedelta(minutes=age_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"lid": lid, "name": name, "latitude": lat, "longitude": lon, "state": {"abbreviation": "MO", "name": "Missouri"},
            "status": {"observed": {"primary": stage, "primaryUnit": "ft", "floodCategory": obs, "validTime": when},
                       "forecast": {"primary": fc_stage, "primaryUnit": "ft", "floodCategory": fc,
                                    "validTime": when if fc != "fcst_not_current" else "0001-01-01T00:00:00Z"}}}


GAUGES = [
    _gauge("MCIM7", "Missouri River at Kansas City", 39.11, -94.59, "minor", "moderate", stage=28.4, fc_stage=31.0),
    _gauge("BLUM7", "Blue River at Kansas City", 39.05, -94.52, "action", stage=9.1),
    _gauge("BRCM7", "Brush Creek at Kansas City", 39.03, -94.58, "no_flooding", stage=2.0),
    _gauge("LBLM7", "Little Blue River near Independence", 39.09, -94.40, "obs_not_current", age_min=60 * 30),
    _gauge("NODM7", "Unrated Creek", 39.20, -94.60, "not_defined", stage=1.0),
    _gauge("FARM7", "Far River", 41.50, -94.00, "major", stage=40.0),
]


def _ms(delta: timedelta) -> int:
    return int((NOW - delta).timestamp() * 1000)


def _fire(name, lat, lon, acres, contained, age=timedelta(days=1)):
    return {"attributes": {"IncidentName": name, "POOState": "US-MO", "POOCounty": "Clay", "IncidentSize": acres,
                           "PercentContained": contained, "ModifiedOnDateTime_dt": _ms(age),
                           "InitialLatitude": lat, "InitialLongitude": lon, "IrwinID": "{x}"}}


FIRES = [
    _fire("PRAIRIE", 39.30, -94.60, 1200, 20),
    _fire("SMALL BRUSH", 39.20, -94.50, 10, 40),
    _fire("OUT FIRE", 39.15, -94.55, 800, 100),
    _fire("OLD FIRE", 39.18, -94.52, 900, 10, age=timedelta(days=20)),
    _fire("FAR FIRE", 41.50, -94.00, 5000, 0),
]


def _portwatch(request: httpx.Request) -> dict:
    q = request.url.params
    where = q["where"]
    start = date.fromisoformat(where.split("date >= '")[1].split("'")[0])
    names = re.findall(r"'([^']+)'", where.split(" AND ")[0])
    last = date(2026, 10, 11)
    rows, day = [], start
    while day <= last:
        for name in names:
            if name == "Strait of Hormuz":
                n = 80 if day < date(2026, 3, 1) else 4
            else:
                n = {"Panama Canal": 25, "Suez Canal": 40}.get(name, 20)
            rows.append({"attributes": {"date": day.isoformat(), "portname": name, "n_total": n}})
        day += timedelta(days=1)
    offset, size = int(q.get("resultOffset", 0)), int(q["resultRecordCount"])
    page = rows[offset: offset + size]
    return {"features": page, **({"exceededTransferLimit": True} if offset + size < len(rows) else {})}


def feeds() -> dict:
    return {
        "api.weather.gov": {
            "features": [
                {"properties": {"id": "a1", "event": "Flood Watch", "severity": "Minor",
                                "headline": "h", "areaDesc": "Jackson", "expires": "2026-10-15T00:00:00Z"}},
                {"properties": {"id": "a2", "event": "Tornado Warning", "severity": "Extreme",
                                "headline": "h2", "areaDesc": "Jackson", "expires": "2026-10-14T13:00:00Z"}},
            ]
        },
        "services.swpc.noaa.gov": [
            ["time_tag", "Kp", "a_running", "station_count"],
            ["2026-10-14 06:00:00.000", "2.33", "9", "8"],
            ["2026-10-14 09:00:00.000", "5.00", "48", "8"],
        ],
        "www.nhc.noaa.gov": {
            "activeStorms": [
                {"id": "al092026", "name": "Nadine", "classification": "HU", "intensity": 80,
                 "pressure": 975, "latitudeNumeric": 25.1, "longitudeNumeric": -80.2,
                 "movementDir": 310, "movementSpeed": 12, "lastUpdate": "2026-10-14T09:00:00.000Z",
                 "publicAdvisory": {"url": "https://www.nhc.noaa.gov/text/x.shtml"}}
            ]
        },
        "www.cisa.gov": {
            "catalogVersion": "2026.10.14",
            "vulnerabilities": [
                {"cveID": "CVE-2026-0001", "vendorProject": "V", "product": "P", "vulnerabilityName": "N",
                 "dateAdded": "2026-10-14", "dueDate": "2026-11-04", "knownRansomwareCampaignUse": "Unknown"},
                {"cveID": "CVE-2020-0002", "vendorProject": "V", "product": "P", "vulnerabilityName": "Old",
                 "dateAdded": "2021-01-01", "dueDate": "2021-02-01", "knownRansomwareCampaignUse": "Known"},
            ],
        },
        "/products/noaa-scales.json": {
            "0": {"R": {"Scale": "0", "Text": "none"}, "S": {"Scale": "0", "Text": "none"}, "G": {"Scale": "1", "Text": "minor"}},
            "1": {"DateStamp": "2026-10-15", "R": {"Scale": None, "MinorProb": "20", "MajorProb": "5"}, "S": {"Scale": None, "Prob": "5"}, "G": {"Scale": "0"}},
        },
        "nasstatus.faa.gov": (
            "<AIRPORT_STATUS_INFORMATION><Delay_type><Name>Ground Delay Programs</Name><Ground_Delay_List>"
            "<Ground_Delay><ARPT>MCI</ARPT><Reason>weather</Reason><Avg>1 hour</Avg></Ground_Delay>"
            "<Ground_Delay><ARPT>BOS</ARPT><Reason>x</Reason></Ground_Delay></Ground_Delay_List></Delay_type>"
            "<Delay_type><Name>Airport Closures</Name><Airport_Closure_List><Airport><ARPT>DEN</ARPT>"
            "<Reason>NOTAM</Reason></Airport></Airport_Closure_List></Delay_type></AIRPORT_STATUS_INFORMATION>"
        ),
        "usdmdataservices.unl.edu": [
            {"mapDate": "2026-10-06T00:00:00", "county": "Jackson County", "none": 0, "d0": 100, "d1": 73.19, "d2": 60.0, "d3": 0, "d4": 0},
            {"mapDate": "2026-09-29T00:00:00", "county": "Jackson County", "none": 0, "d0": 100, "d1": 70, "d2": 8, "d3": 0, "d4": 0},
        ],
        "waterservices.usgs.gov": {"value": {"timeSeries": [
            {"sourceInfo": {"siteCode": [{"value": "02204130"}], "siteName": "HONEY CREEK", "geoLocation": {"geogLocation": {"latitude": 39.0, "longitude": -94.6}}},
             "variable": {"noDataValue": -999999.0},
             "values": [{"value": [{"value": "1.50", "dateTime": "2026-10-13T12:00:00.000-04:00"},
                                   {"value": "-999999", "dateTime": "2026-10-13T12:15:00.000-04:00"},
                                   {"value": "2.50", "dateTime": "2026-10-14T11:00:00.000-04:00"}]}]},
            {"sourceInfo": {"siteCode": [{"value": "99999999"}], "siteName": "FAR CREEK", "geoLocation": {"geogLocation": {"latitude": 33.8, "longitude": -84.9}}},
             "variable": {"noDataValue": -999999.0},
             "values": [{"value": []}]},
        ]}},
        "data.cdc.gov": [
            {"state_territory": "Georgia", "week_end": "2026-10-03T00:00:00.000", "pathogen_target": "SARS-CoV-2", "site_wval_category": "High"},
            {"state_territory": "Georgia", "week_end": "2026-10-03T00:00:00.000", "pathogen_target": "SARS-CoV-2", "site_wval_category": "Very High"},
            {"state_territory": "Georgia", "week_end": "2026-10-03T00:00:00.000", "pathogen_target": "RSV", "site_wval_category": "Low"},
            {"state_territory": "Georgia", "week_end": "2026-09-26T00:00:00.000", "pathogen_target": "RSV", "site_wval_category": "Very High"},
        ],
        "api.fda.gov": {"results": [
            {"recall_number": "F-1", "classification": "Class I", "recalling_firm": "A", "product_description": "p", "reason_for_recall": "r",
             "report_date": "20261013", "distribution_pattern": "Nationwide", "event_id": "1"},
            {"recall_number": "F-2b", "classification": "Class I", "recalling_firm": "A", "product_description": "p2", "reason_for_recall": "r", "report_date": "20261013", "distribution_pattern": "Nationwide", "event_id": "1"},
            {"recall_number": "F-2", "classification": "Class I", "recalling_firm": "B", "product_description": "p", "reason_for_recall": "r",
             "report_date": "20261013", "distribution_pattern": "MN, WI", "event_id": "2"},
            {"recall_number": "F-3", "classification": "Class II", "recalling_firm": "C", "product_description": "p", "reason_for_recall": "r",
             "report_date": "20261001", "distribution_pattern": "Distributed in TX, MO and FL", "event_id": "3"},
        ]},
        "www.saferproducts.gov": [
            {"RecallNumber": "27017", "RecallDate": "2026-10-08T00:00:00", "Title": "Bike recall", "URL": "https://cpsc/x",
             "Hazards": [{"Name": "Lock nut can loosen"}]},
        ],
        "earthquake.usgs.gov": {
            "features": [
                {"id": "us1", "properties": {"mag": 4.6, "place": "10 km N of Somewhere, MO",
                                             "time": int(NOW.timestamp() * 1000) - 3600_000, "url": "https://e/us1"}},
                {"id": "us2", "properties": {"mag": 2.7, "place": "far", "time": int(NOW.timestamp() * 1000) - 5 * 86400_000, "url": "https://e/us2"}},
            ]
        },
        "aviationweather.gov": [
            {"icaoId": "KMCI", "name": "Kansas City Intl, MO, US", "obsTime": int(NOW.timestamp()) - 600, "fltCat": "LIFR", "visib": 1,
             "wspd": 3, "wxString": "BR", "clouds": [{"cover": "BKN", "base": 300}, {"cover": "OVC", "base": 13000}]},
            {"icaoId": "KATL", "name": "Atlanta, GA, US", "obsTime": int(NOW.timestamp()) - 3 * 3600 - 60, "fltCat": "VFR", "visib": "10+", "clouds": []},
        ],
        "api.water.noaa.gov": lambda request: {"gauges": [dict(g) for g in GAUGES]},
        "services9.arcgis.com": _portwatch,
        "services3.arcgis.com": lambda request: {"features": [dict(f) for f in FIRES]},
        "air-quality-api.open-meteo.com": {"current": {
            "time": "2026-10-14T11:00", "us_aqi": 112, "pm2_5": 41.0, "pm10": 50.0, "ozone": 60.0,
            "us_aqi_pm2_5": 112, "us_aqi_pm10": 45, "us_aqi_ozone": 30}},
        "ornl.opendatasoft.com": odin_feed,
        "www.energy.gov": DOE_PAGE,
        "www.nerc.com": {"items": [
            {"documentResult": {"title": "Level 3 Computational Load Alert.pdf", "modifiedDate": "May 4, 2026", "extension": "PDF",
                                "url": "/globalassets/programs/bpsa/alerts/level-3-computational-load-alert.pdf"}},
            {"documentResult": {"title": "NERC Alert Level 2  Large Loads.pdf", "modifiedDate": "September 15, 2025", "extension": "PDF",
                                "url": "/globalassets/programs/bpsa/alerts/2025/nerc-alert-level-2--large-loads.pdf"}},
            {"documentResult": {"title": "Presentation Level 3 Alert Essential Actions Webinar", "modifiedDate": "May 8, 2023",
                                "extension": "PDF", "url": "/globalassets/programs/bpsa/alerts/2023/webinar.pdf"}},
            {"documentResult": {"title": "Inverter Alert", "modifiedDate": "May 8, 2023", "extension": "PDF",
                                "url": "/globalassets/programs/other/inverter-alert.pdf"}},
        ]},
        "www.fema.gov": {
            "DisasterDeclarationsSummaries": [
                {"disasterNumber": 4999, "declarationType": "DR", "declarationTitle": "SEVERE STORMS",
                 "incidentType": "Severe Storm", "declarationDate": "2026-10-01T00:00:00.000Z",
                 "incidentBeginDate": "2026-09-28T00:00:00.000Z", "designatedArea": "Jackson (County)",
                 "iaProgramDeclared": True, "paProgramDeclared": False, "hmProgramDeclared": True},
                {"disasterNumber": 4999, "declarationType": "DR", "declarationTitle": "SEVERE STORMS",
                 "declarationDate": "2026-10-01T00:00:00.000Z", "designatedArea": "Other (County)"},
            ]
        },
    }


def make_client(fail: bool = False, data: dict | None = None) -> httpx.AsyncClient:
    data = data or feeds()

    sent: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.host == "ntfy.sh":
            sent.append(request)
            return httpx.Response(200, json={})
        if fail:
            raise httpx.ConnectError("boom https://api.weather.gov/?point=39.1000,-94.6000")
        payload = data.get(request.url.path) or data[request.url.host]
        if callable(payload):
            payload = payload(request)
        if isinstance(payload, str):
            return httpx.Response(200, text=payload)
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client.sent = sent
    return client


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def config(tmp_path):
    return Config.model_validate({
        "location": {"name": "Kansas City, MO", "lat": 39.1012, "lon": -94.5783, "state": "MO", "county_fips": "29095"},
        "server": {"data_dir": str(tmp_path / "data")},
        "push": {"brief": {}, "context": {}},
        "sources": {"faa_airports": {"params": {"airports": ["mci"]}},
                    "usgs_gauges": {"params": {"flood_stage_ft": {"02204130": 2.0}}}},
    })


@pytest.fixture
def relay(config, clock):
    return Relay(config, client=make_client(), now=clock)


@pytest.fixture
def tokens(relay):
    known = {"brief", "context"}
    return {
        "all": relay.tokens.add("all-job", ["*"], known),
        "brief": relay.tokens.add("brief-job", ["brief"], known),
        "context": relay.tokens.add("ctx-job", ["context"], known),
    }


@pytest.fixture
def api(config, relay):
    with TestClient(create_app(config, relay=relay, start_background=False)) as c:
        yield c


def brief_payload(**over):
    body = {"generated_at": "2026-10-14T11:00:00Z", "model": "claude", "format": "markdown",
            "title": "Daily brief", "body": "All quiet.\x00"}
    body.update(over)
    return body


def auth(token):
    return {"Authorization": f"Bearer {token}"}
