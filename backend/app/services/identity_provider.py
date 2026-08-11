from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode

from authlib.integrations.base_client import OAuthError
from authlib.integrations.starlette_client import OAuth
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import Settings

RETURN_TO_SESSION_KEY = "cassist_auth_return_to"


class IdentityProviderError(Exception):
    pass


@dataclass(frozen=True)
class VerifiedIdentity:
    issuer: str
    subject: str
    email: str
    display_name: str | None
    return_to: str


class IdentityProvider(Protocol):
    async def start_login(
        self,
        request: Request,
        redirect_uri: str,
        return_to: str,
    ) -> Response: ...

    async def complete_login(self, request: Request) -> VerifiedIdentity: ...

    def logout_url(self, return_to: str) -> str: ...


class Auth0IdentityProvider:
    def __init__(self, settings: Settings) -> None:
        if not settings.auth_configured:
            raise ValueError("Authentication is not configured")

        self.issuer = f"{(settings.auth_issuer_url or '').rstrip('/')}/"
        self.client_id = settings.auth_client_id or ""
        self.post_logout_redirect_url = settings.auth_post_logout_redirect_url

        oauth = OAuth()
        self.client = oauth.register(
            name="auth0",
            client_id=self.client_id,
            client_secret=settings.auth_client_secret,
            server_metadata_url=f"{self.issuer}.well-known/openid-configuration",
            client_kwargs={
                "scope": "openid profile email",
                "code_challenge_method": "S256",
            },
        )

    async def start_login(
        self,
        request: Request,
        redirect_uri: str,
        return_to: str,
    ) -> Response:
        request.session[RETURN_TO_SESSION_KEY] = return_to
        return await self.client.authorize_redirect(request, redirect_uri)

    async def complete_login(self, request: Request) -> VerifiedIdentity:
        try:
            token = await self.client.authorize_access_token(request)
        except OAuthError as exc:
            raise IdentityProviderError("The authentication response is invalid") from exc

        claims = token.get("userinfo")
        if not claims or not token.get("id_token"):
            raise IdentityProviderError("The identity provider did not return a valid ID token")

        issuer = claims.get("iss")
        subject = claims.get("sub")
        email = claims.get("email")
        email_verified = claims.get("email_verified")
        if issuer != self.issuer or not subject or not email or email_verified is not True:
            raise IdentityProviderError("The verified identity is incomplete")

        display_name = claims.get("name")
        if not isinstance(display_name, str):
            display_name = None

        return VerifiedIdentity(
            issuer=issuer,
            subject=subject,
            email=email,
            display_name=display_name,
            return_to=request.session.pop(RETURN_TO_SESSION_KEY, "/"),
        )

    def logout_url(self, return_to: str) -> str:
        query = urlencode({"client_id": self.client_id, "returnTo": return_to})
        return f"{self.issuer}v2/logout?{query}"
