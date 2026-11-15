import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import azure.functions as func

from function_app import handle_role_assignment
from rbac_monitor.events import EventValidationError
from tests.fixtures import ASSIGNMENT_ID, SUBSCRIPTION_ID, event_envelope


class FunctionAppTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ, {"EXPECTED_SUBSCRIPTION_ID": SUBSCRIPTION_ID}
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


if __name__ == "__main__":
    unittest.main()
