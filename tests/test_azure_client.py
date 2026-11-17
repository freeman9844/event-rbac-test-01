import json
import unittest
from datetime import datetime, timezone
from email.utils import format_datetime
from unittest.mock import patch

import requests
from azure.core.credentials import AccessToken
from azure.core.exceptions import ClientAuthenticationError

from rbac_monitor.azure_client import ApiError, AzureClient
from tests.fixtures import (
    ASSIGNMENT_ID, PRINCIPAL_ID, ROLE_DEFINITION_ID, SUBSCRIPTION_ID,
)


def assignment_response():
    return {"id": ASSIGNMENT_ID, "properties": {
        "principalId": PRINCIPAL_ID, "principalType": "User",
        "roleDefinitionId": ROLE_DEFINITION_ID, "scope": f"/subscriptions/{SUBSCRIPTION_ID}",
    }}


def user_response():
    return {"id": PRINCIPAL_ID, "displayName": "Workshop User",
            "userPrincipalName": "workshop@example.invalid", "companyName": "company001"}


def response(status=200, body=None, headers=None):
    value = requests.Response()
    value.status_code = status
    value._content = json.dumps(body).encode()
    value.headers.update(headers or {})
    return value


class TokenSource:
    def __init__(self):
        self.scopes = []

    def get_token(self, scope, **kwargs):
        self.scopes.append(scope)
        return AccessToken("private-test-token", 9999999999)


class SequenceSession(requests.Session):
    def __init__(self, replies):
        super().__init__()
        self.replies = list(replies)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        value = self.replies.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class AzureClientTests(unittest.TestCase):
    def client(self, replies):
        self.transport = SequenceSession(replies)
        self.tokens = TokenSource()
        self.waits = []
        return AzureClient(self.tokens, session=self.transport, sleep=self.waits.append)

    def test_fixed_hosts_audiences_select_and_timeout(self):
        client = self.client([response(body=assignment_response()),
                              response(body={"id": ROLE_DEFINITION_ID,
                                             "properties": {"roleName": "Reader"}}),
                              response(body=user_response())])
        client.get_assignment(ASSIGNMENT_ID)
        client.get_role_definition(ROLE_DEFINITION_ID)
        self.assertEqual(client.get_user(PRINCIPAL_ID)["companyName"], "company001")
        self.assertEqual(self.tokens.scopes, [
            "https://management.azure.com/.default",
            "https://management.azure.com/.default", "https://graph.microsoft.com/.default"])
        self.assertEqual(self.transport.calls[0][0],
                         "https://management.azure.com" + ASSIGNMENT_ID)
        self.assertEqual(self.transport.calls[0][1]["params"], {"api-version": "2022-04-01"})
        self.assertEqual(self.transport.calls[2][0],
                         "https://graph.microsoft.com/v1.0/users/" + PRINCIPAL_ID)
        self.assertEqual(self.transport.calls[2][1]["params"],
                         {"$select": "id,displayName,userPrincipalName,companyName"})
        for _, kwargs in self.transport.calls:
            self.assertEqual(kwargs["timeout"], (5, 15))
            self.assertIs(kwargs["allow_redirects"], False)

    def test_forbidden_and_user_not_found_do_not_retry_or_expose_body(self):
        for status in (401, 403, 404):
            with self.subTest(status=status):
                client = self.client([response(status, {"secret": "private-response"})])
                with self.assertRaises(ApiError) as caught:
                    client.get_user(PRINCIPAL_ID)
                self.assertEqual(caught.exception.status_code, status)
                self.assertEqual(caught.exception.stage, "user")
                self.assertNotIn("private-response", str(caught.exception))
                self.assertEqual(len(self.transport.calls), 1)

    def test_assignment_404_and_graph_429_retry_then_succeed(self):
        client = self.client([response(404), response(body=assignment_response())])
        self.assertEqual(client.get_assignment(ASSIGNMENT_ID)["id"], ASSIGNMENT_ID)
        self.assertEqual(self.waits, [1])
        client = self.client([response(429, headers={"Retry-After": "2"}),
                              response(body=user_response())])
        self.assertEqual(client.get_user(PRINCIPAL_ID)["companyName"], "company001")
        self.assertEqual(self.waits, [2])

    def test_retry_after_date_and_excessive_delay(self):
        clock = datetime(2026, 11, 17, tzinfo=timezone.utc)
        header = format_datetime(clock.replace(second=3), usegmt=True)
        client = self.client([response(429, headers={"Retry-After": header}),
                              response(body=user_response())])
        with patch("rbac_monitor.azure_client.datetime", wraps=datetime) as date:
            date.now.return_value = clock
            client.get_user(PRINCIPAL_ID)
        self.assertEqual(self.waits, [3])
        client = self.client([response(429, headers={"Retry-After": "30"})])
        with self.assertRaises(ApiError):
            client.get_user(PRINCIPAL_ID)
        self.assertEqual(self.waits, [])
        self.assertEqual(len(self.transport.calls), 1)

    def test_network_and_service_retries_are_bounded(self):
        client = self.client([requests.ConnectionError("private-response"),
                              response(503), response(503)])
        with self.assertRaises(ApiError) as caught:
            client.get_user(PRINCIPAL_ID)
        self.assertEqual(len(self.transport.calls), 3)
        self.assertEqual(self.waits, [1, 2])
        self.assertNotIn("private-response", str(caught.exception))

    def test_redirects_invalid_json_and_wrong_response_id_fail(self):
        invalid_json = response()
        invalid_json._content = b"private-invalid-json"
        for reply in (response(302, headers={"Location": "https://example.invalid"}),
                      invalid_json, response(body={}),
                      response(body={**user_response(), "id": SUBSCRIPTION_ID}),
                      response(body={**user_response(), "companyName": 123})):
            with self.subTest(reply=reply.status_code):
                client = self.client([reply])
                with self.assertRaises(ApiError):
                    client.get_user(PRINCIPAL_ID)
                self.assertEqual(len(self.transport.calls), 1)

    def test_unsafe_resource_ids_never_reach_transport(self):
        client = self.client([])
        for value in ("https://example.invalid", ASSIGNMENT_ID + "?x=y",
                      ASSIGNMENT_ID.replace("/providers/", "/../providers/")):
            with self.subTest(value=value), self.assertRaises(ApiError):
                client.get_assignment(value)
        self.assertEqual(self.transport.calls, [])

    def test_authentication_failure_is_sanitized(self):
        client = self.client([])
        with patch.object(self.tokens, "get_token",
                          side_effect=ClientAuthenticationError("private-token-error")):
            with self.assertRaises(ApiError) as caught:
                client.get_user(PRINCIPAL_ID)
        self.assertNotIn("private-token-error", str(caught.exception))
        self.assertEqual(self.transport.calls, [])
