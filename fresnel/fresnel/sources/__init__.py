"""Source registry. Add a source: write the class, import it here, list it below."""
from __future__ import annotations

from .base import Context, Result, Source
from .cdc_wastewater import CdcWastewater
from .cisa_kev import CisaKev
from .doe_202c import Doe202c
from .drought import Drought
from .faa_airports import FaaAirports
from .grid_operators import GridOperators
from .nerc_alerts import NercAlerts
from .nhc_storms import NhcStorms
from .nws_alerts import NwsAlerts
from .odin_outages import OdinOutages
from .openfema import OpenFema
from .recalls import CpscRecalls, FdaRecalls
from .swpc_kp import SwpcKp
from .swpc_scales import SwpcScales
from .usgs_gauges import UsgsGauges
from .usgs_quakes import UsgsQuakes

REGISTRY: dict[str, type[Source]] = {
    cls.id: cls for cls in (
        NwsAlerts, OpenFema, UsgsQuakes, UsgsGauges, Drought, FaaAirports, CdcWastewater,
        FdaRecalls, CpscRecalls, CisaKev, SwpcKp, SwpcScales, NhcStorms,
        OdinOutages, Doe202c, NercAlerts, GridOperators,
    )
}

__all__ = ["REGISTRY", "Context", "Result", "Source"]
