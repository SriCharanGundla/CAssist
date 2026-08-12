from datetime import datetime

from pydantic import BaseModel


class ViewOriginalResponse(BaseModel):
    url: str
    expires_at: datetime
