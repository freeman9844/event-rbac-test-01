import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

from scripts import workshop
from tests.test_workshop_artifacts import azure_base, outputs_fixture
from tests.test_workshop_state import state_fixture


SUB = "/subscriptions/11111111-1111-4111-8111-111111111111"
GROUP = SUB + "/resourceGroups/rg-rbac-workshop-abcd1234"
READER = SUB + "/providers/Microsoft.Authorization/roleDefinitions/acdd72a7-3385-48ef-bd42-f606fba81ae7"
USER = "33333333-3333-4333-8333-333333333333"
EXTERNAL = SUB + "/providers/Microsoft.Authorization/roleAssignments/88888888-8888-4888-8888-888888888888"


class Clock:
    def __init__(self):
        self.seconds = 0

    def monotonic(self):
        return self.seconds

    def sleep(self, seconds):
        self.seconds += seconds


class Azure:
    def __init__(self, path):
        self.path = path
        self.roles = {}
        self.group_present = True
        self.forbidden = False
        self.wrong_tags = False
        self.create_error = False
        self.corrupt_property = None
        self.delete_error = False
        self.stuck_group = False
        self.stuck_role = False
        self.delay = 0
        self.log_calls = 0
        self.success = True
        self.unrelated = False
        self.bad_window = False
        self.failure_total = None
        self.metric_error = None
        self.deployment_reader = EXTERNAL
        self.forbid_roles_after_create = False
        self.roles_forbidden = False

    def role(self, resource_id, principal=USER, scope=GROUP, principal_type="User"):
        return {"id": resource_id, "name": resource_id.rsplit("/", 1)[1],
                "type": "Microsoft.Authorization/roleAssignments",
                "properties": {"principalId": principal, "principalType": principal_type,
                               "roleDefinitionId": READER, "scope": scope}}

    def __call__(self, args, **kwargs):
        assert args[args.index("--subscription") + 1] == SUB.rsplit("/", 1)[1]
        if self.forbidden:
            raise workshop.WorkshopError("AuthorizationFailed 403")
        if args[:2] == ["group", "exists"]:
            return self.group_present
        if args[:2] == ["group", "show"]:
            result = azure_base(args)
            if self.wrong_tags:
                result["tags"]["runId"] = "99999999"
            return result
        if args[:2] == ["group", "delete"]:
            if not self.stuck_group:
                self.group_present = False
                self.roles = {key: value for key, value in self.roles.items()
                              if value["properties"]["scope"] != GROUP}
            return None
        if args[:3] == ["deployment", "sub", "show"]:
            outputs = outputs_fixture()
            outputs["subscriptionReaderAssignmentId"] = self.deployment_reader
            return {"properties": {"provisioningState": "Succeeded", "outputs": {
                key: {"type": "String", "value": value} for key, value in outputs.items()
            }}}
        if args[0] == "rest":
            method = args[args.index("--method") + 1].lower()
            resource_id = urlparse(args[args.index("--url") + 1]).path
            if resource_id.endswith("/roleAssignments"):
                if self.roles_forbidden:
                    raise workshop.WorkshopError("Role query denied 403")
                return {"value": copy.deepcopy(list(self.roles.values()))}
            if "/roleAssignments/" in resource_id:
                if method == "put":
                    saved = json.loads(self.path.read_text(encoding="utf-8"))
                    assert saved["assignment"]["id"] == resource_id, "Intent was not persisted."
                    body = json.loads(args[args.index("--body") + 1])
                    result = self.role(resource_id)
                    result["properties"].update(body["properties"])
                    if self.corrupt_property:
                        key, value = self.corrupt_property
                        result["properties"][key] = value
                    self.roles[resource_id] = result
                    self.roles_forbidden = self.forbid_roles_after_create
                    if self.create_error:
                        raise workshop.WorkshopError("Create response lost")
                    return copy.deepcopy(result)
                if method == "delete":
                    if self.delete_error:
                        raise workshop.WorkshopError("Delete denied 403")
                    if not self.stuck_role:
                        self.roles.pop(resource_id, None)
                    return None
                return copy.deepcopy(self.roles[resource_id])
            if "/workspaces/" in resource_id:
                return {"id": resource_id, "properties": {
                    "customerId": "99999999-9999-4999-8999-999999999999"}}
        if args[:3] == ["monitor", "activity-log", "list"]:
            assignment = workshop.load_state(self.path)["assignment"]["id"]
            return [{
                "resourceId": assignment,
                "eventTimestamp": "2026-11-15T12:00:01Z",
                "operationName": {"value": "Microsoft.Authorization/roleAssignments/write"},
                "status": {"value": "Started"},
                "correlationId": "activity-correlation",
                "claims": {"secret": "do-not-copy"},
                "properties": {"requestbody": json.dumps({"Properties": {
                    "PrincipalId": USER, "RoleDefinitionId": READER, "secret": "do-not-copy"}})},
            }, {
                "resourceId": assignment,
                "eventTimestamp": "2026-11-15T12:00:02Z",
                "operationName": {"value": "Microsoft.Authorization/roleAssignments/write"},
                "status": {"value": "Succeeded"}, "correlationId": "activity-correlation",
                "properties": {},
            }, {
                "resourceId": assignment,
                "eventTimestamp": "2026-11-15T12:00:04Z",
                "operationName": {"value": "Microsoft.Authorization/roleAssignments/delete"},
                "status": {"value": "Succeeded"}, "correlationId": "delete-correlation",
                "properties": {},
            }]
        if args[:3] == ["monitor", "log-analytics", "query"]:
            self.log_calls += 1
            assignment = workshop.load_state(self.path)["assignment"]["id"]
            assert assignment in args[args.index("--analytics-query") + 1]
            assert args[args.index("--timespan") + 1] == "2026-11-15T11:59:00Z/2026-11-15T12:01:00Z"
            if self.log_calls <= self.delay:
                return []
            return [{"RoleAssignmentId": GROUP + "/other" if self.unrelated else assignment,
                     "EventId": "event-not-activity-correlation",
                     "InvocationId": "invocation-one", "OperationId": "operation-one",
                     "ReceivedAtUtc": "2026-11-15T12:00:03Z",
                     "IngestedAtUtc": "2026-11-15T12:00:33Z",
                     "RequestAtUtc": "2026-11-15T12:00:03Z",
                     "Success": self.success, "DurationMs": 36.2}]
        if args[:3] == ["monitor", "metrics", "list"]:
            start = args[args.index("--start-time") + 1]
            end = args[args.index("--end-time") + 1]
            if self.bad_window:
                end = "2026-11-15T21:01:00Z"
            result = {"timespan": start + "/" + end, "value": [
                {"name": {"value": name}, "timeseries": [{"data": [{"total": total}]}]}
                for name, total in (("PublishSuccessCount", 2), ("MatchedEventCount", 1),
                                    ("DeliverySuccessCount", 1),
                                    ("DeliveryAttemptFailCount", self.failure_total))
            ]}
            if self.metric_error is not None:
                result["value"][-1].update(
                    errorCode=self.metric_error, errorMessage="Metric provider failure",
                    timeseries=[])
            return result
        return azure_base(args)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "abcd1234.json"
        state = state_fixture()
        state["outputs"] = outputs_fixture()
        workshop.save_state(self.path, state)
        self.azure = Azure(self.path)
        self.clock = Clock()
        for target, replacement in (
            ("run_az", self.azure), ("now_utc", lambda: "2026-11-15T12:00:00Z"),
        ):
            patcher = patch.object(workshop, target, side_effect=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Only the clock boundary is accelerated; files and lifecycle logic remain real.
        for name in ("monotonic", "sleep"):
            patcher = patch("time." + name, side_effect=getattr(self.clock, name))
            patcher.start()
            self.addCleanup(patcher.stop)

    def verify(self, **kwargs):
        with patch.object(workshop, "now_utc", return_value="2026-11-15T12:01:00Z"):
            return workshop.verify_event(self.path, **kwargs)

    def test_exercise_persists_intent_and_removes_only_its_assignment(self):
        result = workshop.exercise_event(self.path)
        saved = workshop.load_state(self.path)["assignment"]
        self.assertTrue(result["created"])
        self.assertTrue(result["removed"])
        self.assertEqual(saved, result)
        self.assertTrue(saved["id"].startswith(GROUP + "/providers/Microsoft.Authorization/roleAssignments/"))
        self.assertEqual(saved["requestedAtUtc"], "2026-11-15T12:00:00Z")
        self.assertEqual(self.azure.roles, {})
        with self.assertRaises(workshop.WorkshopError):
            workshop.exercise_event(self.path)

    def test_existing_reader_is_neither_replaced_nor_deleted(self):
        existing = GROUP + "/providers/Microsoft.Authorization/roleAssignments/44444444-4444-4444-8444-444444444444"
        self.azure.roles[existing] = self.azure.role(existing)
        with self.assertRaises(workshop.WorkshopError):
            workshop.exercise_event(self.path)
        self.assertEqual(list(self.azure.roles), [existing])
        self.assertIsNone(workshop.load_state(self.path)["assignment"])

    def test_lost_create_response_is_an_error_but_reserved_id_is_cleaned(self):
        self.azure.create_error = True
        with self.assertRaisesRegex(workshop.WorkshopError, "Create response lost"):
            workshop.exercise_event(self.path)
        self.assertEqual(self.azure.roles, {})
        self.assertTrue(workshop.load_state(self.path)["assignment"]["removed"])

    def test_mismatched_assignment_is_not_deleted(self):
        cases = [("principalId", "44444444-4444-4444-8444-444444444444"),
                 ("scope", SUB),
                 ("roleDefinitionId", READER.replace("acdd72a7", "bcdd72a7"))]
        for key, value in cases:
            with self.subTest(key=key):
                state = state_fixture()
                state["outputs"] = outputs_fixture()
                workshop.save_state(self.path, state)
                self.azure.roles.clear()
                self.azure.corrupt_property = (key, value)
                with self.assertRaises(workshop.WorkshopError):
                    workshop.exercise_event(self.path)
                self.assertEqual(len(self.azure.roles), 1)
                self.assertFalse(workshop.load_state(self.path)["assignment"]["removed"])

    def test_delete_failure_keeps_recoverable_state(self):
        self.azure.delete_error = True
        with self.assertRaisesRegex(workshop.WorkshopError, "403"):
            workshop.exercise_event(self.path)
        self.assertEqual(len(self.azure.roles), 1)
        self.assertFalse(workshop.load_state(self.path)["assignment"]["removed"])

    def test_role_query_forbidden_after_creation_keeps_recovery_id(self):
        self.azure.forbid_roles_after_create = True
        with self.assertRaisesRegex(workshop.WorkshopError, "403"):
            workshop.exercise_event(self.path)
        saved = workshop.load_state(self.path)["assignment"]
        self.assertIn(saved["id"], self.azure.roles)
        self.assertFalse(saved["removed"])

    def test_malformed_assignment_state_is_rejected_before_cloud_actions(self):
        workshop.exercise_event(self.path)
        original = json.loads(self.path.read_text(encoding="utf-8"))
        for key, value in (("id", None), ("created", 1), ("requestedAtUtc", "2026-11-15T12:00:00"),
                           ("id", EXTERNAL), ("name", "injected;command")):
            with self.subTest(key=key, value=value):
                state = copy.deepcopy(original)
                state["assignment"][key] = value
                self.path.write_text(json.dumps(state), encoding="utf-8")
                with self.assertRaises(workshop.WorkshopError):
                    workshop.cleanup_run(self.path, None, dry_run=True)
                self.assertTrue(self.azure.group_present)

    def test_verify_waits_and_saves_redacted_correlated_evidence(self):
        workshop.exercise_event(self.path)
        self.azure.delay = 1
        summary = self.verify(timeout_seconds=40, interval_seconds=20)
        self.assertTrue(summary["verified"])
        self.assertEqual(summary["function"]["eventId"], "event-not-activity-correlation")
        self.assertEqual(summary["function"]["invocationId"], "invocation-one")
        self.assertEqual(summary["function"]["durationMs"], 36.2)
        self.assertEqual(summary["metrics"]["DeliveryAttemptFailCount"], None)
        self.assertEqual(summary["metrics"]["DeliverySuccessCount"], 1)
        evidence = self.path.parent / "abcd1234"
        persisted = json.loads((evidence / "verification.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted, summary)
        self.assertEqual(summary["window"], {"startUtc": "2026-11-15T11:59:00Z",
                                            "endUtc": "2026-11-15T12:01:00Z"})
        self.assertNotIn("do-not-copy", "".join(path.read_text(encoding="utf-8")
                                              for path in evidence.glob("*.json")))
        self.azure.failure_total = 0
        self.assertEqual(self.verify()["metrics"]["DeliveryAttemptFailCount"], 0)

    def test_other_assignment_or_failed_function_cannot_verify(self):
        workshop.exercise_event(self.path)
        for failure in ("unrelated", "success"):
            with self.subTest(failure=failure):
                self.azure.unrelated = failure == "unrelated"
                self.azure.success = failure != "success"
                with self.assertRaisesRegex(workshop.WorkshopError, "[Tt]ime"):
                    self.verify(timeout_seconds=20, interval_seconds=20)
                evidence = self.path.parent / "abcd1234" / "verification.json"
                self.assertFalse(json.loads(evidence.read_text(encoding="utf-8"))["verified"])

    def test_metric_window_mismatch_is_not_reported_as_success(self):
        workshop.exercise_event(self.path)
        self.azure.bad_window = True
        with self.assertRaisesRegex(workshop.WorkshopError, "[Tt]imespan"):
            self.verify()
        evidence = self.path.parent / "abcd1234" / "verification.json"
        self.assertFalse(json.loads(evidence.read_text(encoding="utf-8"))["verified"])

    def test_per_metric_failure_is_not_an_empty_success_sample(self):
        workshop.exercise_event(self.path)
        self.azure.metric_error = "BadRequest"
        with self.assertRaisesRegex(workshop.WorkshopError, "BadRequest"):
            self.verify()
        evidence = self.path.parent / "abcd1234" / "verification.json"
        self.assertFalse(json.loads(evidence.read_text(encoding="utf-8"))["verified"])
        self.azure.metric_error = "Success"
        self.assertIsNone(self.verify()["metrics"]["DeliveryAttemptFailCount"])

    def test_capture_cannot_forget_a_reader_that_cleanup_still_needs(self):
        state = workshop.load_state(self.path)
        state["outputs"]["subscriptionReaderAssignmentId"] = EXTERNAL
        workshop.save_state(self.path, state)
        original = self.path.read_bytes()
        self.azure.roles[EXTERNAL] = self.azure.role(
            EXTERNAL, principal="55555555-5555-4555-8555-555555555555",
            scope=SUB, principal_type="ServicePrincipal")
        self.azure.deployment_reader = ""
        with self.assertRaisesRegex(workshop.WorkshopError, "Reader"):
            workshop.capture_deployment(self.path, "rbac-ws-abcd1234-infra")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertIn(EXTERNAL, self.azure.roles)
        result = workshop.cleanup_run(
            self.path, "DELETE abcd1234 FROM 11111111-1111-4111-8111-111111111111")
        self.assertEqual(result["subscriptionReader"], "removed")
        self.assertEqual(self.azure.roles, {})

    def test_capture_forgets_old_reader_only_after_proving_absence(self):
        state = workshop.load_state(self.path)
        state["outputs"]["subscriptionReaderAssignmentId"] = EXTERNAL
        workshop.save_state(self.path, state)
        original = self.path.read_bytes()
        self.azure.deployment_reader = ""
        self.azure.roles_forbidden = True
        with self.assertRaisesRegex(workshop.WorkshopError, "403"):
            workshop.capture_deployment(self.path, "rbac-ws-abcd1234-infra")
        self.assertEqual(self.path.read_bytes(), original)
        self.azure.roles_forbidden = False
        workshop.capture_deployment(self.path, "rbac-ws-abcd1234-infra")
        self.assertEqual(workshop.load_state(self.path)["outputs"]["subscriptionReaderAssignmentId"], "")

    def test_cleanup_preview_refusal_and_owned_group_deletion(self):
        confirmation = "DELETE abcd1234 FROM 11111111-1111-4111-8111-111111111111"
        preview = workshop.cleanup_run(self.path, None, dry_run=True)
        self.assertEqual(preview["confirmation"], confirmation)
        self.assertTrue(self.azure.group_present)
        with self.assertRaises(workshop.WorkshopError):
            workshop.cleanup_run(self.path, "DELETE everything")
        self.assertTrue(self.azure.group_present)
        result = workshop.cleanup_run(self.path, confirmation)
        self.assertTrue(result["resourceGroupRemoved"])
        self.assertFalse(self.azure.group_present)
        self.assertTrue(workshop.cleanup_run(self.path, confirmation)["resourceGroupRemoved"])

    def test_cleanup_prevalidates_all_ownership_before_any_deletion(self):
        state = workshop.load_state(self.path)
        state["outputs"]["subscriptionReaderAssignmentId"] = EXTERNAL
        workshop.save_state(self.path, state)
        self.azure.roles[EXTERNAL] = self.azure.role(EXTERNAL, principal=USER, scope=SUB)
        confirmation = "DELETE abcd1234 FROM 11111111-1111-4111-8111-111111111111"
        with self.assertRaises(workshop.WorkshopError):
            workshop.cleanup_run(self.path, confirmation)
        self.assertTrue(self.azure.group_present)
        self.assertIn(EXTERNAL, self.azure.roles)

    def test_cleanup_removes_recorded_reader_even_after_group_disappears(self):
        state = workshop.load_state(self.path)
        state["outputs"]["subscriptionReaderAssignmentId"] = EXTERNAL
        workshop.save_state(self.path, state)
        self.azure.roles[EXTERNAL] = self.azure.role(
            EXTERNAL, principal="55555555-5555-4555-8555-555555555555",
            scope=SUB, principal_type="ServicePrincipal")
        self.azure.group_present = False
        result = workshop.cleanup_run(
            self.path, "DELETE abcd1234 FROM 11111111-1111-4111-8111-111111111111")
        self.assertTrue(result["resourceGroupRemoved"])
        self.assertEqual(self.azure.roles, {})

    def test_partial_run_can_clean_tagged_group_without_guessing_external_roles(self):
        state = state_fixture()
        workshop.save_state(self.path, state)
        self.azure.roles[EXTERNAL] = self.azure.role(EXTERNAL, principal=USER, scope=SUB)
        result = workshop.cleanup_run(
            self.path, "DELETE abcd1234 FROM 11111111-1111-4111-8111-111111111111")
        self.assertTrue(result["resourceGroupRemoved"])
        self.assertEqual(result["subscriptionReader"], "not-recorded")
        self.assertIn(EXTERNAL, self.azure.roles)

    def test_forbidden_or_wrong_tags_are_not_absence(self):
        for issue in ("forbidden", "wrong_tags"):
            with self.subTest(issue=issue):
                self.azure.forbidden = issue == "forbidden"
                self.azure.wrong_tags = issue == "wrong_tags"
                with self.assertRaises(workshop.WorkshopError):
                    workshop.cleanup_run(self.path, None, dry_run=True)
                self.assertTrue(self.azure.group_present)

    def test_cleanup_and_role_deletion_timeouts_are_explicit(self):
        self.azure.stuck_group = True
        with self.assertRaisesRegex(workshop.WorkshopError, "[Tt]ime"):
            workshop.cleanup_run(
                self.path, "DELETE abcd1234 FROM 11111111-1111-4111-8111-111111111111")
        self.assertTrue(self.azure.group_present)
        self.azure.stuck_role = True
        with self.assertRaisesRegex(workshop.WorkshopError, "[Tt]ime"):
            workshop.exercise_event(self.path)
        self.assertFalse(workshop.load_state(self.path)["assignment"]["removed"])


class EmptyResponseTests(unittest.TestCase):
    def test_empty_output_is_allowed_only_when_explicitly_requested(self):
        result = subprocess.CompletedProcess(["az"], 0, stdout="", stderr="")
        with patch.object(workshop.shutil, "which", return_value="az"), \
                patch.object(workshop.subprocess, "run", return_value=result):
            with self.assertRaises(workshop.WorkshopError):
                workshop.run_az(["account", "show"])
            self.assertIsNone(workshop.run_az(["group", "delete"], allow_empty=True))
