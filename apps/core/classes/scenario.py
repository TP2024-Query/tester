import uuid
from typing import TypedDict, Optional, List, Dict, Any


class ScenarioJson(TypedDict):
    id: uuid.UUID
    url: str
    status_code: int
    ignored_properties: Optional[List[str]]
    messages: Optional[List[str]]
    depends_on: Optional[List[uuid.UUID]]
    additional_data: Optional[Dict[str, Any]]
    body: Optional[Dict[str, Any]]
    method: Optional[str]
    response: str
