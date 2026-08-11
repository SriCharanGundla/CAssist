from uuid import UUID

from pydantic import BaseModel

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
