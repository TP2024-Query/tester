import uuid
from enum import Enum
from typing import TypedDict, Optional, List, Dict, Any


class ScenarioStatus(str, Enum):
    OK: str = "ok"
    INVALID: str = "invalid"
    TIMEOUT: str = "timeout"
    SKIPPED: str = "skipped"
    ERROR: str = "error"

    def __str__(self):
        return self.value


class ScenarioResultJson(TypedDict):
    id: uuid.UUID
    url: str
    status: ScenarioStatus
    status_code: int
    ignored_properties: Optional[List[str]]
    messages: Optional[List[str]]
    diff: Optional[str]
    additional_data: Optional[Dict[str, Any]]
    duration: str
    response: str
