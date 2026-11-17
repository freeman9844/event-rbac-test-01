import copy
import unittest

from rbac_monitor.azure_client import ApiError, AzureClient
from rbac_monitor.events import parse_event
from rbac_monitor.processor import observe
from tests.fixtures import (
    ASSIGNMENT_ID, PRINCIPAL_ID, ROLE_DEFINITION_ID, SUBSCRIPTION_ID, event_envelope,
)
from tests.test_azure_client import (
    SequenceSession, TokenSource, assignment_response, response, user_response,
)


class ProcessorTests(unittest.TestCase):
    def observe(self, assignment=None, user=None, role=None):
        self.transport = SequenceSession([
            response(body=assignment if assignment is not None else assignment_response()),
            response(body=role if role is not None else {
                "id": ROLE_DEFINITION_ID, "properties": {"roleName": "Reader"}}),
            response(body=user if user is not None else user_response()),
        ])
        client = AzureClient(TokenSource(), session=self.transport, sleep=lambda _: None)
        return observe(parse_event(event_envelope(), SUBSCRIPTION_ID), client)

    def test_user_company_name_is_observed(self):
        result = self.observe()
        self.assertEqual(result["eventName"], "RbacRoleAssignmentObserved")
        self.assertEqual(result["principalId"], PRINCIPAL_ID)
        self.assertEqual(result["roleAssignmentId"], ASSIGNMENT_ID)
        self.assertEqual(result["companyName"], "company001")
        self.assertEqual(result["roleName"], "Reader")
        self.assertEqual(result["scope"], f"/subscriptions/{SUBSCRIPTION_ID}")
        self.assertEqual(result["enrichmentStatus"], "succeeded")
        self.assertTrue(result["observedAt"].endswith("Z"))
        self.assertNotIn("private-actor-claim", str(result))
        self.assertEqual(result["correlationId"], "correlation-001")

    def test_missing_company_name_is_not_api_failure(self):
        for company in (None, ""):
            with self.subTest(company=company):
                result = self.observe(user={**user_response(), "companyName": company})
                self.assertEqual(result["enrichmentStatus"], "companyNameNotSet")
                self.assertEqual(result["companyName"], company)

    def test_non_user_skips_graph(self):
        for kind in ("Group", "ServicePrincipal"):
            assignment = assignment_response()
            assignment["properties"]["principalType"] = kind
            with self.subTest(kind=kind):
                result = self.observe(assignment=assignment)
                self.assertEqual(result["enrichmentStatus"], "notApplicable")
                self.assertIsNone(result["companyName"])
                self.assertIsNone(result["userPrincipalName"])
                self.assertEqual(len(self.transport.calls), 2)

    def test_invalid_assignment_fields_or_scope_fail(self):
        for key, value in (("principalId", "not-a-uuid"), ("principalType", None),
                           ("scope", "/subscriptions/" + PRINCIPAL_ID),
                           ("roleDefinitionId", "https://example.invalid")):
            assignment = copy.deepcopy(assignment_response())
            assignment["properties"][key] = value
            with self.subTest(key=key), self.assertRaises(ApiError):
                self.observe(assignment=assignment)
        with self.assertRaises(ApiError):
            self.observe(assignment={"id": ASSIGNMENT_ID})

    def test_missing_user_select_field_and_empty_role_name_fail(self):
        user = user_response()
        del user["companyName"]
        with self.assertRaises(ApiError):
            self.observe(user=user)
        with self.assertRaises(ApiError):
            self.observe(role={"id": ROLE_DEFINITION_ID, "properties": {"roleName": ""}})
