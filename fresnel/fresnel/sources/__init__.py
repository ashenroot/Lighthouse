"""Source registry. Add a source: write the class, import it here, list it below."""
from __future__ import annotations

from .base import Context, Result, Source
from .cdc_wastewater import CdcWastewater
from .cisa_kev import CisaKev
from .drought import Drought
from .faa_airports import FaaAirports
from .nhc_storms import NhcStorms
from .nws_alerts import NwsAlerts
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
    )
}

__all__ = ["REGISTRY", "Context", "Result", "Source"]
