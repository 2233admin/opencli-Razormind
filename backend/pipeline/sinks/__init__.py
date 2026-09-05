"""Write sinks: the destination seam for collected items.

Import the seam from here:

    from backend.pipeline.sinks import (
        LegacyDbSink,
        OdpSink,
        DualSink,
        ItemSink,
        RunContext,
        SinkResult,
    )
"""

from backend.pipeline.sinks.base import (
    CollectionLineage,
    ItemSink,
    RunContext,
    SinkResult,
)
from backend.pipeline.sinks.dual_sink import DualSink
from backend.pipeline.sinks.legacy_db_sink import LegacyDbSink
from backend.pipeline.sinks.odp_sink import OdpSink
from backend.pipeline.sinks.strategy import select_sink

__all__ = [
    "CollectionLineage",
    "ItemSink",
    "RunContext",
    "SinkResult",
    "LegacyDbSink",
    "OdpSink",
    "DualSink",
    "select_sink",
]
