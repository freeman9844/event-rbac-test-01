import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from uuid import UUID

import requests
from azure.core.credentials import TokenCredential
from azure.core.exceptions import ClientAuthenticationError


class ApiError(RuntimeError):
    def __init__(self, stage: str, code: str, *, status_code: int | None = None,
                 request_id: str | None = None):
        self.stage = stage
        self.code = code
        self.status_code = status_code
        self.request_id = request_id
        super().__init__(f"{stage}: {code}")


def _uuid(value: object, stage: str) -> str:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value.lower():
            raise ValueError
    except ValueError:
        raise ApiError(stage, "invalidIdentifier") from None
    return value.lower()


def _arm_path(value: str, kind: str) -> str:
    if not isinstance(value, str):
        raise ApiError("validation", "invalidResourceId")
    parts = value.split("/")
    if (len(parts) < 5 or parts[0] != ""
            or any(part in ("", ".", "..") for part in parts[1:])
            or any(c in value for c in ("\\", "?", "#", "%"))
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or [part.lower() for part in parts[-4:-1]]
            != ["providers", "microsoft.authorization", kind]):
        raise ApiError("validation", "invalidResourceId")
    _uuid(parts[-1], "validation")
    if parts[1].lower() == "subscriptions":
        _uuid(parts[2], "validation")
    elif kind == "roleassignments" or parts[1].lower() != "providers":
        raise ApiError("validation", "invalidResourceId")
    return value


class AzureClient:
    def __init__(self, credential: TokenCredential, *,
                 session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.credential = credential
        self.session = session if session is not None else requests.Session()
        self.sleep = sleep

    def _get(self, host: str, path: str, params: dict, stage: str, *,
             retry_not_found: bool = False) -> dict:
        for attempt in range(3):
            try:
                token = self.credential.get_token(host + "/.default").token
            except ClientAuthenticationError:
                raise ApiError(stage, "authenticationFailed") from None
            try:
                response = self.session.get(
                    host + path, params=params,
                    headers={"Authorization": "Bearer " + token},
                    timeout=(5, 15), allow_redirects=False,
                )
            except (requests.ConnectionError, requests.Timeout):
                if attempt == 2:
                    raise ApiError(stage, "networkError") from None
                self.sleep(2 ** attempt)
                continue
            request_id = response.headers.get("request-id") or response.headers.get("x-ms-request-id")
            if request_id and re.fullmatch(r"[a-zA-Z0-9._-]{1,128}", request_id) is None:
                request_id = None
            status = response.status_code
            if status == 200:
                try:
                    body = response.json()
                except ValueError:
                    raise ApiError(stage, "invalidResponse", status_code=200,
                                   request_id=request_id) from None
                if not isinstance(body, dict):
                    raise ApiError(stage, "invalidResponse", status_code=200,
                                   request_id=request_id)
                return body
            retryable = status in (408, 429, 500, 502, 503, 504) or (
                retry_not_found and status == 404)
            if not retryable or attempt == 2:
                raise ApiError(stage, f"http{status}", status_code=status, request_id=request_id)
            delay = 2 ** attempt
            header = response.headers.get("Retry-After")
            if header is not None:
                try:
                    if header.isdigit():
                        delay = int(header)
                    else:
                        retry_at = parsedate_to_datetime(header)
                        if retry_at.tzinfo is None:
                            raise ValueError
                        delay = max(0, (retry_at - datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    raise ApiError(stage, "invalidRetryAfter", status_code=status,
                                   request_id=request_id) from None
            if delay > 10:
                raise ApiError(stage, "retryAfterExceedsBudget", status_code=status,
                               request_id=request_id)
            self.sleep(delay)
        raise AssertionError("Unreachable retry state")

    def _arm_get(self, resource_id: str, kind: str, stage: str) -> dict:
        path = _arm_path(resource_id, kind)
        body = self._get("https://management.azure.com", path,
                         {"api-version": "2022-04-01"}, stage,
                         retry_not_found=kind == "roleassignments")
        if (not isinstance(body.get("id"), str)
                or body["id"].lower() != resource_id.lower()
                or not isinstance(body.get("properties"), dict)):
            raise ApiError(stage, "invalidResponse")
        return body

    def get_assignment(self, resource_id: str) -> dict:
        return self._arm_get(resource_id, "roleassignments", "roleAssignment")

    def get_role_definition(self, resource_id: str) -> dict:
        body = self._arm_get(resource_id, "roledefinitions", "roleDefinition")
        name = body["properties"].get("roleName")
        if not isinstance(name, str) or not name.strip():
            raise ApiError("roleDefinition", "invalidResponse")
        return body

    def get_user(self, principal_id: str) -> dict:
        principal_id = _uuid(principal_id, "user")
        body = self._get("https://graph.microsoft.com", "/v1.0/users/" + principal_id,
                         {"$select": "id,displayName,userPrincipalName,companyName"}, "user")
        if _uuid(body.get("id"), "user") != principal_id:
            raise ApiError("user", "responseIdentityMismatch")
        for key in ("displayName", "userPrincipalName", "companyName"):
            if key not in body or (body[key] is not None and not isinstance(body[key], str)):
                raise ApiError("user", "invalidResponse")
        return body
