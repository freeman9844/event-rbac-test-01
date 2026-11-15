import unittest

from rbac_monitor.events import EventValidationError, parse_event
from tests.fixtures import (
    ASSIGNMENT_ID,
    ASSIGNMENT_NAME,
    SUBSCRIPTION_ID,
    event_envelope,
)


class EventTests(unittest.TestCase):
    def test_accepts_subscription_assignment(self):
        event = parse_event(event_envelope(), SUBSCRIPTION_ID)
        self.assertEqual(event.role_assignment_id, ASSIGNMENT_ID)
        self.assertEqual(event.subscription_id, SUBSCRIPTION_ID)
        self.assertEqual(event.event_id, "event-001")
        self.assertEqual(event.event_time, "2026-11-15T09:00:00Z")
        self.assertEqual(event.correlation_id, "correlation-001")

    def test_accepts_assignment_at_each_scope_case_insensitively(self):
        suffix = (
            f"/providers/Microsoft.Authorization/roleAssignments/{ASSIGNMENT_NAME}"
        )
        scopes = [
            f"/subscriptions/{SUBSCRIPTION_ID}",
            f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/test-rg",
            f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/test-rg"
            "/providers/Microsoft.Storage/storageAccounts/teststorage",
        ]
        for scope in scopes:
            with self.subTest(scope=scope):
                resource_id = (scope + suffix).upper()
                envelope = event_envelope(resource_id)
                envelope["topic"] = f"/SUBSCRIPTIONS/{SUBSCRIPTION_ID.upper()}"
                event = parse_event(envelope, SUBSCRIPTION_ID.upper())
                self.assertEqual(event.role_assignment_id, resource_id)
                self.assertEqual(event.subscription_id, SUBSCRIPTION_ID)

    def test_falls_back_to_resource_uri_only_without_subject(self):
        for missing in (None, ""):
            with self.subTest(subject=missing):
                envelope = event_envelope()
                envelope["subject"] = missing
                self.assertEqual(
                    parse_event(envelope, SUBSCRIPTION_ID).role_assignment_id,
                    ASSIGNMENT_ID,
                )

    def test_accepts_subject_without_optional_resource_uri(self):
        envelope = event_envelope()
        data = envelope["data"]
        assert isinstance(data, dict)
        data.pop("resourceUri")
        data.pop("correlationId")
        event = parse_event(envelope, SUBSCRIPTION_ID)
        self.assertEqual(event.role_assignment_id, ASSIGNMENT_ID)
        self.assertIsNone(event.correlation_id)

    def test_rejects_mismatched_ids_foreign_subscription_and_url(self):
        alternatives = [
            ASSIGNMENT_ID.replace(ASSIGNMENT_NAME, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            ASSIGNMENT_ID.replace(SUBSCRIPTION_ID, "33333333-3333-4333-8333-333333333333"),
            "https://example.invalid" + ASSIGNMENT_ID,
        ]
        for resource_uri in alternatives:
            with self.subTest(resource_uri=resource_uri):
                envelope = event_envelope()
                data = envelope["data"]
                assert isinstance(data, dict)
                data["resourceUri"] = resource_uri
                with self.assertRaises(EventValidationError):
                    parse_event(envelope, SUBSCRIPTION_ID)

    def test_rejects_paths_that_are_not_safe_assignment_resources(self):
        invalid_ids = [
            ASSIGNMENT_ID + "?api-version=other",
            ASSIGNMENT_ID + "#fragment",
            ASSIGNMENT_ID.replace("/providers/", "/../providers/", 1),
            ASSIGNMENT_ID.replace("/providers/", "/%2e%2e/providers/", 1),
            ASSIGNMENT_ID.replace("/providers/", "\\providers\\", 1),
            ASSIGNMENT_ID.replace("/roleAssignments/", "/roleDefinitions/"),
            ASSIGNMENT_ID.replace(ASSIGNMENT_NAME, "not-a-guid"),
            ASSIGNMENT_ID.replace(SUBSCRIPTION_ID, "33333333-3333-4333-8333-333333333333"),
            "https://example.invalid" + ASSIGNMENT_ID,
        ]
        for resource_id in invalid_ids:
            with self.subTest(resource_id=resource_id):
                with self.assertRaises(EventValidationError):
                    parse_event(event_envelope(resource_id), SUBSCRIPTION_ID)

    def test_rejects_wrong_source_topic(self):
        for topic in (
            None,
            f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/not-the-source",
            "/subscriptions/33333333-3333-4333-8333-333333333333",
        ):
            with self.subTest(topic=topic):
                envelope = event_envelope()
                envelope["topic"] = topic
                with self.assertRaises(EventValidationError):
                    parse_event(envelope, SUBSCRIPTION_ID)

    def test_rejects_non_rbac_operation(self):
        envelope = event_envelope()
        data = envelope["data"]
        assert isinstance(data, dict)
        data["operationName"] = "Microsoft.Storage/storageAccounts/write"
        with self.assertRaises(EventValidationError):
            parse_event(envelope, SUBSCRIPTION_ID)

    def test_rejects_wrong_event_type_and_malformed_data(self):
        for field, value in (
            ("eventType", "Microsoft.Resources.ResourceWriteFailure"),
            ("data", None),
            ("data", ["not-an-object"]),
        ):
            with self.subTest(field=field, value=value):
                envelope = event_envelope()
                envelope[field] = value
                with self.assertRaises(EventValidationError):
                    parse_event(envelope, SUBSCRIPTION_ID)

    def test_rejects_missing_or_invalid_required_identifiers_and_times(self):
        for field, value in (
            ("id", None),
            ("id", " "),
            ("eventTime", None),
            ("eventTime", "not-a-time"),
            ("eventTime", "2026-11-15T09:00:00"),
            ("subject", ["not-a-path"]),
        ):
            with self.subTest(field=field, value=value):
                envelope = event_envelope()
                envelope[field] = value
                with self.assertRaises(EventValidationError):
                    parse_event(envelope, SUBSCRIPTION_ID)

    def test_normalizes_timestamp_to_utc(self):
        envelope = event_envelope()
        envelope["eventTime"] = "2026-11-15T18:00:00+09:00"
        self.assertEqual(
            parse_event(envelope, SUBSCRIPTION_ID).event_time,
            "2026-11-15T09:00:00Z",
        )


if __name__ == "__main__":
    unittest.main()
