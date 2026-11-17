import copy
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts import workshop
from tests.test_workshop_state import state_fixture


def outputs_fixture():
    scope = ("/subscriptions/11111111-1111-4111-8111-111111111111"
             "/resourceGroups/rg-rbac-workshop-abcd1234")
    return {
        "functionAppName": "func-rbac-lab-aaaaaaaaaaaaa",
        "functionAppResourceId": scope + "/providers/Microsoft.Web/sites/func-rbac-lab-aaaaaaaaaaaaa",
        "managedIdentityPrincipalId": "55555555-5555-4555-8555-555555555555",
        "applicationInsightsName": "appi-rbac-lab-aaaaaaaaaaaaa",
        "workspaceResourceId": scope + "/providers/Microsoft.OperationalInsights/workspaces/log-rbac-lab-aaaaaaaaaaaaa",
        "systemTopicName": "evgt-rbac-lab-aaaaaaaaaaaaa",
        "subscriptionReaderAssignmentId": "",
    }


def azure_base(args):
    state = state_fixture()
    assert "--subscription" in args, args
    if args[:2] == ["account", "show"]:
        return {"id": state["subscriptionId"], "tenantId": state["tenantId"],
                "user": {"type": "user"}}
    if args[:3] == ["ad", "signed-in-user", "show"]:
        return {"id": state["userObjectId"]}
    if args[:2] == ["group", "exists"]:
        return True
    if args[:2] == ["group", "show"]:
        return {"id": ("/subscriptions/" + state["subscriptionId"]
                       + "/resourceGroups/" + state["resourceGroupName"]),
                "tags": {"workshop": "rbac-event", "runId": "abcd1234",
                         "managedBy": "workshop", "policyTag": "allowed"}}
    if args[:3] == ["rest", "--method", "get"]:
        return {"id": outputs_fixture()["functionAppResourceId"],
                "identity": {"principalId": outputs_fixture()["managedIdentityPrincipalId"]},
                "properties": {"state": "Running"}}
    raise AssertionError(f"Unexpected Azure command: {args}")


class ArtifactTests(unittest.TestCase):
    def test_capture_keeps_only_validated_deployment_outputs(self):
        state = state_fixture()
        deployment = {"properties": {"provisioningState": "Succeeded", "outputs": {
            key: {"type": "String", "value": value}
            for key, value in outputs_fixture().items()
        }}}
        def azure(args):
            return deployment if args[:3] == ["deployment", "sub", "show"] else azure_base(args)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            workshop.save_state(path, state)
            with patch.object(workshop, "run_az", side_effect=azure):
                workshop.capture_deployment(path, "rbac-ws-abcd1234-infra")
            saved = workshop.load_state(path)
        self.assertEqual(saved["outputs"], outputs_fixture())

    def test_foreign_or_failed_deployment_never_replaces_run_outputs(self):
        cases = [
            ("functionAppResourceId", outputs_fixture()["functionAppResourceId"].replace(
                "rg-rbac-workshop-abcd1234", "rg-production")),
            ("managedIdentityPrincipalId", "not-a-uuid"),
            ("managedIdentityPrincipalId", "66666666-6666-4666-8666-666666666666"),
            ("status", "Failed"),
            ("deploymentName", "another-run"),
        ]
        for key, value in cases:
            outputs = copy.deepcopy(outputs_fixture())
            status = "Succeeded"
            name = "rbac-ws-abcd1234-infra"
            if key == "status":
                status = value
            elif key == "deploymentName":
                name = value
            else:
                outputs[key] = value
            deployment = {"properties": {"provisioningState": status, "outputs": {
                k: {"type": "String", "value": v} for k, v in outputs.items()
            }}}
            def azure(args):
                return deployment if args[:3] == ["deployment", "sub", "show"] else azure_base(args)
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "state.json"
                workshop.save_state(path, state_fixture())
                original = path.read_bytes()
                with patch.object(workshop, "run_az", side_effect=azure):
                    with self.assertRaises(workshop.WorkshopError):
                        workshop.capture_deployment(path, name)
                self.assertEqual(path.read_bytes(), original)

    def test_zip_contains_only_sources_at_linux_package_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = ["function_app.py", "host.json", "requirements.txt",
                       "rbac_monitor/__init__.py", "rbac_monitor/events.py",
                       "rbac_monitor/azure_client.py", "rbac_monitor/processor.py"]
            excluded = ["local.settings.json", ".venv/token.py", ".python_packages/a.py",
                        ".workshop-state/run.json", "docs/guide.md", "tests/test_a.py",
                        "rbac_monitor/__pycache__/events.pyc", "scripts/companyname.py"]
            for name in sources + excluded:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("test data", encoding="utf-8")
            destination = root / "artifacts" / "receiver.zip"
            workshop.package_sources(root, destination)
            with zipfile.ZipFile(destination) as archive:
                self.assertEqual(sorted(archive.namelist()), sorted(sources))
                self.assertEqual(archive.read("function_app.py"), b"test data")

    def test_zip_refuses_symbolic_source_before_creating_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "rbac_monitor").mkdir()
            for name in ("function_app.py", "host.json", "requirements.txt",
                         "rbac_monitor/__init__.py"):
                (root / name).write_text("data", encoding="utf-8")
            with patch.object(Path, "is_symlink", autospec=True,
                              side_effect=lambda path: path.name == "function_app.py"):
                with self.assertRaises(workshop.WorkshopError):
                    workshop.package_sources(root, root / "receiver.zip")
            self.assertFalse((root / "receiver.zip").exists())
