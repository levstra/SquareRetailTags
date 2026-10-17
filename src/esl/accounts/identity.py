from typing import Protocol

import boto3
from botocore.exceptions import ClientError

from esl.config import settings


class IdentityError(Exception):
    """The identity provider (Cognito) call failed."""


class EmailAlreadyRegistered(IdentityError):
    """An account with this email already exists in the user pool."""


class IdentityProvider(Protocol):
    def invite(self, email: str) -> str: ...
    def disable(self, email: str) -> None: ...
    def enable(self, email: str) -> None: ...
    def delete(self, email: str) -> None: ...


class CognitoIdentityProvider:
    """Cognito admin calls. The pool uses email as the username attribute."""

    def __init__(self, *, region: str, user_pool_id: str, client=None):
        self._pool = user_pool_id
        self._client = client or boto3.client("cognito-idp", region_name=region)

    def invite(self, email: str) -> str:
        try:
            response = self._client.admin_create_user(
                UserPoolId=self._pool,
                Username=email,
                UserAttributes=[
                    {"Name": "email", "Value": email},
                    {"Name": "email_verified", "Value": "true"},
                ],
                DesiredDeliveryMediums=["EMAIL"],
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "UsernameExistsException":
                raise EmailAlreadyRegistered(email) from exc
            raise IdentityError(str(exc)) from exc
        attributes = {a["Name"]: a["Value"] for a in response["User"]["Attributes"]}
        return attributes["sub"]

    def disable(self, email: str) -> None:
        self._admin("admin_disable_user", email)

    def enable(self, email: str) -> None:
        self._admin("admin_enable_user", email)

    def delete(self, email: str) -> None:
        self._admin("admin_delete_user", email)

    def _admin(self, operation: str, email: str) -> None:
        try:
            getattr(self._client, operation)(UserPoolId=self._pool, Username=email)
        except ClientError as exc:
            raise IdentityError(str(exc)) from exc


def make_identity_provider() -> IdentityProvider:
    if not (settings.cognito_region and settings.cognito_user_pool_id):
        raise IdentityError("Cognito is not configured (COGNITO_REGION, COGNITO_USER_POOL_ID)")
    return CognitoIdentityProvider(
        region=settings.cognito_region, user_pool_id=settings.cognito_user_pool_id
    )
