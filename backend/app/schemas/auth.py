from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import MemberRole


class AuthUserResponse(BaseModel):
    id: UUID
    email: str
    display_name: str | None


class WorkspaceMembershipResponse(BaseModel):
    id: UUID
    name: str
    role: MemberRole


class CurrentAuthResponse(BaseModel):
    user: AuthUserResponse
    workspaces: list[WorkspaceMembershipResponse]


class CsrfTokenResponse(BaseModel):
    csrf_token: str


class LogoutResponse(BaseModel):
    logout_url: str


class AuthSessionResponse(BaseModel):
    id: UUID
    device_label: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    is_current: bool


class AuthSessionPageResponse(BaseModel):
    items: list[AuthSessionResponse]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=10)
    total: int = Field(ge=0)
    total_pages: int = Field(ge=0)
