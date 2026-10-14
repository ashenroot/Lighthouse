import json
from datetime import datetime, timezone

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
