import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import workshop


ROOT = Path(__file__).resolve().parents[1]


def state_fixture():
    return {
        "schemaVersion": 1,
        "runId": "abcd1234",
        "subscriptionId": "11111111-1111-4111-8111-111111111111",
        "tenantId": "22222222-2222-4222-8222-222222222222",
        "userObjectId": "33333333-3333-4333-8333-333333333333",
        "resourceGroupName": "rg-rbac-workshop-abcd1234",
        "namePrefix": "rbac-lab",
        "location": "koreacentral",
        "createdAtUtc": "2026-01-01T00:00:00Z",
        "deploymentNames": {
            "infra": "rbac-ws-abcd1234-infra",
            "events": "rbac-ws-abcd1234-events",
        },
        "outputs": {},
        "assignment": None,
    }


class StateTests(unittest.TestCase):
    def test_show_prints_a_valid_restored_run_without_cloud_access(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "abcd1234.json"
            path.write_text(json.dumps(state_fixture()), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "workshop.py"), "show", str(path)],
                capture_output=True, text=True, cwd=directory,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["resourceGroupName"],
            "rg-rbac-workshop-abcd1234",
        )

    def test_load_rejects_foreign_or_command_shaped_state(self):
        cases = [
            ("subscriptionId", "../another"),
            ("tenantId", "not-a-uuid"),
            ("runId", "../../home"),
            ("resourceGroupName", "rg-production"),
            ("location", "koreacentral; echo unsafe"),
            ("namePrefix", "$(command)"),
            ("schemaVersion", True),
            ("createdAtUtc", "2026-01-01T00:00:00"),
            ("outputs", []),
            ("accessToken", "must-not-be-saved"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            for key, value in cases:
                state = state_fixture()
                state[key] = value
                path.write_text(json.dumps(state), encoding="utf-8")
                with self.subTest(key=key):
                    with self.assertRaises(workshop.WorkshopError):
                        workshop.load_state(path)

    def test_save_validates_before_replacing_existing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            original = json.dumps(state_fixture())
            path.write_text(original, encoding="utf-8")
            invalid = state_fixture()
            invalid["resourceGroupName"] = "rg-production"
            with self.assertRaises(workshop.WorkshopError):
                workshop.save_state(path, invalid)
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            workshop.save_state(path, state_fixture())
            self.assertEqual(workshop.load_state(path)["runId"], "abcd1234")

    def azure(self, args):
        state = state_fixture()
        self.assertIn("--subscription", args)
        if args[:2] == ["account", "show"]:
            return {"id": state["subscriptionId"], "tenantId": state["tenantId"],
                    "user": {"type": "user"}}
        if args[:3] == ["ad", "signed-in-user", "show"]:
            return {"id": state["userObjectId"]}
        if args[:2] == ["rest", "--method"]:
            return {"value": []}
        if args[:2] == ["group", "exists"]:
            return False
        self.fail(f"Unexpected Azure command: {args}")

    def test_init_creates_private_restorable_state_but_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(workshop, "run_az", side_effect=self.azure):
                with patch.object(workshop.secrets, "token_hex", return_value="abcd1234"):
                    state = state_fixture()
                    path = workshop.init_run(Path(directory), state["subscriptionId"],
                                             state["tenantId"], "koreacentral")
                    self.assertEqual(path.name, "abcd1234.json")
                    self.assertEqual(workshop.load_state(path)["assignment"], None)
                    original = path.read_bytes()
                    with self.assertRaises(workshop.WorkshopError):
                        workshop.init_run(Path(directory), state["subscriptionId"],
                                          state["tenantId"], "koreacentral")
                    self.assertEqual(path.read_bytes(), original)

    def test_init_stops_on_foreign_source_topic_in_last_page_or_query_failure(self):
        state = state_fixture()
        url = ("https://management.azure.com/subscriptions/" + state["subscriptionId"]
               + "/providers/Microsoft.EventGrid/systemTopics?api-version=2025-02-15")
        for failure in ("topic", "403"):
            def azure(args):
                if args[:2] == ["rest", "--method"]:
                    if failure == "403":
                        raise workshop.WorkshopError("AuthorizationFailed 403")
                    if args[args.index("--url") + 1] == url:
                        return {"value": [], "nextLink": url + "&$skiptoken=next"}
                    return {"value": [{"id": "/another-rg/topic", "properties": {
                        "source": "/subscriptions/" + state["subscriptionId"],
                        "topicType": "Microsoft.Resources.Subscriptions",
                        "provisioningState": "Succeeded",
                    }}]}
                return self.azure(args)
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                with patch.object(workshop, "run_az", side_effect=azure):
                    with self.assertRaises(workshop.WorkshopError):
                        workshop.init_run(Path(directory), state["subscriptionId"],
                                          state["tenantId"], "koreacentral")
                self.assertFalse((Path(directory) / ".workshop-state").exists())

    def test_check_rejects_foreign_identity_and_foreign_owned_group(self):
        state = state_fixture()
        for failure in ("tenant", "user", "tags", "service-principal"):
            def azure(args):
                value = self.azure(args) if args[:2] != ["group", "show"] else {}
                if failure == "tenant" and args[:2] == ["account", "show"]:
                    value["tenantId"] = "44444444-4444-4444-8444-444444444444"
                if failure == "service-principal" and args[:2] == ["account", "show"]:
                    value["user"]["type"] = "servicePrincipal"
                if failure == "user" and args[:3] == ["ad", "signed-in-user", "show"]:
                    value["id"] = "44444444-4444-4444-8444-444444444444"
                if failure == "tags" and args[:2] == ["group", "exists"]:
                    return True
                if args[:2] == ["group", "show"]:
                    return {"id": "/subscriptions/" + state["subscriptionId"]
                            + "/resourceGroups/rg-rbac-workshop-abcd1234",
                            "tags": {"workshop": "rbac-event", "runId": "ffffffff",
                                     "managedBy": "workshop"}}
                return value
            with self.subTest(failure=failure):
                with patch.object(workshop, "run_az", side_effect=azure):
                    with self.assertRaises(workshop.WorkshopError):
                        workshop.check_run(state)

    def test_cli_errors_and_non_json_are_not_empty_successes(self):
        results = [
            subprocess.CompletedProcess(["az"], 1, "", "AuthorizationFailed"),
            subprocess.CompletedProcess(["az"], 0, "not json", ""),
            subprocess.TimeoutExpired(["az"], 90),
        ]
        for result in results:
            with self.subTest(result=type(result).__name__):
                with patch("shutil.which", return_value="az"):
                    with patch("subprocess.run") as execute:
                        if isinstance(result, Exception):
                            execute.side_effect = result
                        else:
                            execute.return_value = result
                        with self.assertRaises(workshop.WorkshopError):
                            workshop.run_az(["group", "exists"])
