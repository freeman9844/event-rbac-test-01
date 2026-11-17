import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import azure.functions as func

from function_app import handle_role_assignment
from rbac_monitor.azure_client import ApiError
from rbac_monitor.events import EventValidationError
from tests.fixtures import ASSIGNMENT_ID, ROLE_DEFINITION_ID, SUBSCRIPTION_ID, event_envelope
from tests.test_azure_client import (
    SequenceSession, TokenSource, assignment_response, response, user_response,
)


class FunctionAppTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ, {"EXPECTED_SUBSCRIPTION_ID": SUBSCRIPTION_ID,
                         "ENABLE_IDENTITY_ENRICHMENT": "false"}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def sdk_event(self, event_type="Microsoft.Resources.ResourceWriteSuccess"):
        data = event_envelope()["data"]
        assert isinstance(data, dict)
        return func.EventGridEvent(
            id="event-001",
            data=data,
            topic=f"/subscriptions/{SUBSCRIPTION_ID}",
            subject=ASSIGNMENT_ID,
            event_type=event_type,
            event_time=datetime(2026, 11, 15, 9, tzinfo=timezone.utc),
            data_version="2",
        )

    def test_logs_received_identity_without_claims_or_entire_payload(self):
        with self.assertLogs("rbac_monitor", level="INFO") as captured:
            handle_role_assignment(self.sdk_event())
        record = json.loads(captured.records[-1].getMessage())
        self.assertEqual(record["eventName"], "RbacRoleAssignmentReceived")
        self.assertEqual(record["eventId"], "event-001")
        self.assertEqual(record["roleAssignmentId"], ASSIGNMENT_ID)
        self.assertNotIn("private-actor-claim", "\n".join(captured.output))
        self.assertNotIn("authorization", record)
        self.assertNotIn("data", record)

    def test_invalid_input_logs_error_and_reraises(self):
        with self.assertLogs("rbac_monitor", level="ERROR") as captured:
            with self.assertRaises(EventValidationError):
                handle_role_assignment(
                    self.sdk_event("Microsoft.Resources.ResourceWriteFailure")
                )
        record = json.loads(captured.records[-1].getMessage())
        self.assertEqual(record["eventName"], "RbacRoleAssignmentEnrichmentFailed")
        self.assertEqual(record["eventId"], "event-001")
        self.assertNotIn("RbacRoleAssignmentReceived", "\n".join(captured.output))
        self.assertNotIn("private-actor-claim", "\n".join(captured.output))

    def test_missing_subscription_configuration_logs_error_and_fails(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertLogs("rbac_monitor", level="ERROR") as captured:
                with self.assertRaisesRegex(ValueError, "EXPECTED_SUBSCRIPTION_ID"):
                    handle_role_assignment(self.sdk_event())
        self.assertNotIn("RbacRoleAssignmentReceived", "\n".join(captured.output))

    def test_disabled_enrichment_never_constructs_cloud_client(self):
        with patch("function_app.ManagedIdentityCredential", create=True,
                   side_effect=AssertionError("Disabled mode must not acquire credentials")):
            with self.assertLogs("rbac_monitor", level="INFO") as captured:
                handle_role_assignment(self.sdk_event())
        self.assertEqual(len(captured.records), 1)
        self.assertEqual(json.loads(captured.records[0].getMessage())["eventName"],
                         "RbacRoleAssignmentReceived")

    def enriched_call(self, user_reply):
        transport = SequenceSession([
            response(body=assignment_response()),
            response(body={"id": ROLE_DEFINITION_ID, "properties": {"roleName": "Reader"}}),
            user_reply,
        ])
        with patch.dict(os.environ, {"ENABLE_IDENTITY_ENRICHMENT": "true"}):
            with patch("function_app.ManagedIdentityCredential", create=True) as credential:
                credential.return_value.__enter__.return_value = TokenSource()
                with patch("requests.Session", return_value=transport):
                    handle_role_assignment(self.sdk_event())

    def test_enabled_enrichment_logs_observed_company_name(self):
        with self.assertLogs("rbac_monitor", level="INFO") as captured:
            self.enriched_call(response(body=user_response()))
        record = json.loads(captured.records[-1].getMessage())
        self.assertEqual(record["eventName"], "RbacRoleAssignmentObserved")
        self.assertEqual(record["companyName"], "company001")
        self.assertEqual(record["enrichmentStatus"], "succeeded")
        self.assertNotIn("private-actor-claim", "\n".join(captured.output))
        self.assertNotIn("private-test-token", "\n".join(captured.output))

    def test_graph_forbidden_logs_failure_and_reraises(self):
        with self.assertLogs("rbac_monitor", level="INFO") as captured:
            with self.assertRaises(ApiError):
                self.enriched_call(response(403, {"secret": "private-response"}))
        record = json.loads(captured.records[-1].getMessage())
        self.assertEqual(record["eventName"], "RbacRoleAssignmentEnrichmentFailed")
        self.assertEqual(record["stage"], "user")
        self.assertEqual(record["httpStatus"], 403)
        self.assertEqual(record["roleAssignmentId"], ASSIGNMENT_ID)
        self.assertNotIn("RbacRoleAssignmentObserved", "\n".join(captured.output))
        self.assertNotIn("private-response", "\n".join(captured.output))

    def test_invalid_enrichment_setting_fails(self):
        with patch.dict(os.environ, {"ENABLE_IDENTITY_ENRICHMENT": "tru"}):
            with self.assertLogs("rbac_monitor", level="ERROR") as captured:
                with self.assertRaises(ValueError):
                    handle_role_assignment(self.sdk_event())
        self.assertEqual(json.loads(captured.records[-1].getMessage())["stage"], "configuration")


if __name__ == "__main__":
    unittest.main()
