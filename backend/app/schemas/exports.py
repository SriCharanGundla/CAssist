from typing import Literal

from pydantic import BaseModel, Field


class TallyExportOptions(BaseModel):
    include_quality_issues: bool = True


class CreateExportRequest(BaseModel):
    expected_version: int = Field(ge=1)
    format: Literal["tally_json"]
    options: TallyExportOptions = Field(default_factory=TallyExportOptions)
