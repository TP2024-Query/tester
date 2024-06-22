import uuid
from enum import Enum
from typing import TypedDict, List

from apps.core.classes.scenario import ScenarioJson


class Status(str, Enum):
    PENDING: str = "pending"
    DONE: str = "done"
    FAILED: str = "failed"

    def __str__(self):
        return self.value


class TaskJson(TypedDict):
    id: uuid.UUID
    docker_image: str
    db_name: str
    status: Status
    scenarios: List[ScenarioJson]
