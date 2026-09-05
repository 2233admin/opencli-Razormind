from datetime import datetime

from backend.schemas.common import UTCModel


class WorkerNodeRead(UTCModel):
    id: str
    worker_id: str
    hostname: str
    status: str
    active_tasks: int
    last_heartbeat: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
