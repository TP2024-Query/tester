import uuid
from enum import Enum
from typing import TypedDict, List

from apps.core.classes.scenario_record import ScenarioResultJson
from apps.core.classes.task import Status


class TaskResultJson(TypedDict):
    id: uuid.UUID
    docker_image: str
    db_name: str
    status: str
    message: str
    output: str
    scenario_results: List[ScenarioResultJson]
