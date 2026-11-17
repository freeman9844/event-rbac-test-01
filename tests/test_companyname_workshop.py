import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote

from scripts import companyname, workshop
from tests.test_workshop_artifacts import azure_base, outputs_fixture
from tests.test_workshop_state import state_fixture
from tests.test_azure_client import response


USER_ID = "44444444-4444-4444-8444-444444444444"
GRAPH_ID = "77777777-7777-4777-8777-777777777777"
GRANT_ID = "88888888-8888-4888-8888-888888888888"
APP_ROLE_ID = "df021288-bdef-4463-88db-98f22de89214"
GLOBAL_ADMIN = "62e90394-69f5-4237-9190-012177145e10"


class Cloud:
    def __init__(self, base):
        self.base = base
        self.roles = {}
        self.user = None
        self.grants = []
        self.roles_templates = [GLOBAL_ADMIN]
        self.writes = []
        self.lost_user_response = False
        self.fail_grant_delete = False
        self.last_user_body = None
        self.pending_queries = 0
        self.query_error = None
        self.row_overrides = {}
        self.query_snapshots = []
        self.lost_role_response = False
        self.pending_grant_reads = 0
        self.hide_grants = False
        self.lost_grant_response = False
        reader_id = base["outputs"]["subscriptionReaderAssignmentId"]
        self.roles[reader_id] = {"id": reader_id, "properties": {
            "principalId": base["outputs"]["managedIdentityPrincipalId"],
            "principalType": "ServicePrincipal", "scope": workshop.subscription_scope(base),
            "roleDefinitionId": workshop.role_definition(base),
        }}

    def items(self, path, params=None, *, advanced=False):
        return self.request("GET", path, params=params, advanced=advanced)["value"]

    def request(self, method, path, *, body=None, params=None, missing_ok=False,
                arm=False, advanced=False):
        if method != "GET":
            self.writes.append((method, path))
        if arm:
            if method == "PUT":
                role = {"id": path, "properties": {**body["properties"],
                                                  "scope": workshop.group_scope(self.base)}}
                self.roles[path] = role
                if self.lost_role_response:
                    raise workshop.WorkshopError("simulated lost role response")
                return role
            raise AssertionError((method, path))
        if path == "/v1.0/me":
            return {"id": self.base["userObjectId"]}
        if path == "/v1.0/me/transitiveMemberOf/microsoft.graph.directoryRole":
            return {"value": [{"roleTemplateId": value} for value in self.roles_templates]}
        if path == "/v1.0/organization":
            return {"value": [{"id": self.base["tenantId"], "verifiedDomains": [{
                "name": "workshop.onmicrosoft.com", "isInitial": True, "type": "Managed",
            }]}]}
        if path.startswith("/v1.0/servicePrincipals(appId="):
            return {"id": GRAPH_ID, "appId": "00000003-0000-0000-c000-000000000000",
                    "appRoles": [{"id": APP_ROLE_ID, "value": "User.Read.All",
                                  "isEnabled": True, "allowedMemberTypes": ["Application"]}]}
        if path.endswith("/appRoleAssignments"):
            if self.hide_grants:
                return {"value": []}
            if self.pending_grant_reads:
                self.pending_grant_reads -= 1
                return {"value": []}
            return {"value": copy.deepcopy(self.grants)}
        if path.endswith("/appRoleAssignedTo") and method == "POST":
            grant = {"id": GRANT_ID, **body}
            self.grants.append(grant)
            if self.lost_grant_response:
                raise workshop.WorkshopError("simulated lost grant response")
            return grant
        if "/appRoleAssignedTo/" in path and method == "DELETE":
            if self.fail_grant_delete:
                raise workshop.WorkshopError("simulated grant deletion failure")
            self.grants = [g for g in self.grants if g["id"] != path.rsplit("/", 1)[1]]
            return None
        if path == "/v1.0/users" and method == "POST":
            self.last_user_body = copy.deepcopy(body)
            self.user = {key: value for key, value in body.items() if key != "passwordProfile"}
            self.user.update(id=USER_ID, createdDateTime=workshop.now_utc())
            if self.lost_user_response:
                raise workshop.WorkshopError("simulated lost create response")
            return {"id": USER_ID}
        if path.startswith("/v1.0/users/"):
            key = unquote(path.rsplit("/", 1)[1])
            if self.user is None or key not in (self.user["id"], self.user["userPrincipalName"]):
                if missing_ok:
                    return None
                raise workshop.WorkshopError("User not found")
            if method == "GET":
                return copy.deepcopy(self.user)
            if method == "DELETE":
                self.user = None
                return None
        raise AssertionError((method, path))

    def az(self, args, **kwargs):
        test_role = next((role for role in self.roles.values()
                          if role["properties"]["principalId"] == USER_ID), None)
        if args[:3] == ["monitor", "activity-log", "list"]:
            return [{
                "resourceId": test_role["id"], "eventTimestamp": workshop.now_utc(),
                "operationName": {"value": "Microsoft.Authorization/roleAssignments/write"},
                "status": {"value": "Succeeded"}, "correlationId": "activity-advanced",
                "properties": {"requestbody": {"properties": test_role["properties"]}},
            }]
        if args[:3] == ["monitor", "log-analytics", "query"]:
            self.query_snapshots.append((self.user is not None, test_role is not None))
            if self.query_error:
                raise self.query_error
            if self.pending_queries:
                self.pending_queries -= 1
                return []
            timestamp = workshop.now_utc()
            return [{
                "EventName": "RbacRoleAssignmentObserved",
                "RoleAssignmentId": test_role["id"], "PrincipalId": USER_ID,
                "PrincipalType": "User", "RoleDefinitionId": workshop.role_definition(self.base),
                "RoleName": "Reader", "Scope": workshop.group_scope(self.base),
                "CompanyName": "company001", "EnrichmentStatus": "succeeded",
                "EventId": "event-advanced", "InvocationId": "invocation-advanced",
                "OperationId": "operation-advanced", "RequestInvocationId": "invocation-advanced",
                "RequestOperationId": "operation-advanced", "Success": True, "DurationMs": 20,
                "ObservedAtUtc": timestamp, "LookupAtUtc": timestamp,
                "IngestedAtUtc": timestamp, "RequestAtUtc": timestamp, **self.row_overrides,
            }]
        if args[:3] == ["rest", "--method", "get"]:
            url = args[args.index("--url") + 1]
            if "/roleAssignments?" in url:
                return {"value": list(self.roles.values())}
            if self.base["outputs"]["workspaceResourceId"] in url:
                return {"id": self.base["outputs"]["workspaceResourceId"],
                        "properties": {"customerId": "99999999-9999-4999-8999-999999999999"}}
        if args[:3] == ["rest", "--method", "delete"]:
            url = args[args.index("--url") + 1]
            self.roles.pop(url.removeprefix("https://management.azure.com").split("?")[0])
            return None
        return azure_base(args)


class CompanyNameWorkshopTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "abcd1234.json"
        self.base = state_fixture()
        self.base["outputs"] = outputs_fixture()
        self.base["outputs"]["subscriptionReaderAssignmentId"] = (
            workshop.subscription_scope(self.base)
            + "/providers/Microsoft.Authorization/roleAssignments/"
            "66666666-6666-4666-8666-666666666666")
        workshop.save_state(self.path, self.base)
        self.before = self.path.read_bytes()
        self.cloud = Cloud(self.base)
        self.original_roles = copy.deepcopy(self.cloud.roles)
        context = patch.object(workshop, "now_utc", return_value="2026-11-17T07:00:00Z")
        context.start()
        self.addCleanup(context.stop)
        for target, kwargs in (
            ("scripts.companyname.OperatorClient", {"return_value": self.cloud}),
            ("scripts.workshop.run_az", {"side_effect": self.cloud.az}),
        ):
            context = patch(target, **kwargs)
            context.start()
            self.addCleanup(context.stop)

    def companion(self):
        return json.loads(self.path.with_name("abcd1234-companyname.json").read_text())

    def test_reader_only_admin_preflight_blocks_writes(self):
        self.cloud.roles_templates = ["f2ef992c-3afb-46b9-b7cf-a126ee74c451"]
        with self.assertRaises(workshop.WorkshopError):
            companyname.preflight(self.path)
        self.assertEqual(self.cloud.writes, [])

    def test_supported_admin_role_combination_is_accepted(self):
        self.cloud.roles_templates = [
            "fe930be7-5e62-47db-91af-98c3a49a38b1",
            "e8611ab8-c189-46e8-94e1-60213ab1f814"]
        self.assertEqual(companyname.preflight(self.path)["domain"], "workshop.onmicrosoft.com")
        self.assertEqual(self.cloud.writes, [])

    def test_prepare_creates_disabled_new_user_with_company001(self):
        companyname.prepare(self.path, "company001")
        self.assertEqual(self.cloud.last_user_body["companyName"], "company001")
        self.assertIs(self.cloud.last_user_body["accountEnabled"], False)
        self.assertEqual(self.companion()["user"]["id"], USER_ID)
        saved = self.path.with_name("abcd1234-companyname.json").read_text()
        self.assertNotIn("passwordProfile", saved)
        self.assertNotIn(self.cloud.last_user_body["passwordProfile"]["password"], saved)
        self.assertEqual(len(self.cloud.grants), 1)

    def test_operator_identity_is_never_replaced(self):
        companyname.prepare(self.path, "company001")
        companyname.cleanup(self.path)
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(self.cloud.roles, self.original_roles)
        self.assertIsNone(self.cloud.user)
        self.assertEqual(self.cloud.grants, [])

    def test_existing_user_is_not_adopted(self):
        self.cloud.user = {"id": USER_ID,
                           "userPrincipalName": "rbac-companyname-abcd1234@workshop.onmicrosoft.com"}
        with self.assertRaises(workshop.WorkshopError):
            companyname.prepare(self.path, "company001")
        self.assertEqual(self.cloud.writes, [])

    def test_foreign_state_is_rejected(self):
        companyname.prepare(self.path, "company001")
        value = self.companion()
        value["operatorObjectId"] = USER_ID
        workshop.save_json(self.path.with_name("abcd1234-companyname.json"), value)
        before = list(self.cloud.writes)
        with self.assertRaises(workshop.WorkshopError):
            companyname.cleanup(self.path)
        self.assertEqual(self.cloud.writes, before)

    def test_response_loss_preserves_reconciliation_identity(self):
        self.cloud.lost_user_response = True
        with self.assertRaisesRegex(workshop.WorkshopError, "lost create response"):
            companyname.prepare(self.path, "company001")
        companyname.cleanup(self.path)
        self.assertEqual(self.companion()["user"]["id"], USER_ID)
        self.assertIsNone(self.cloud.user)
        self.assertEqual(self.cloud.grants, [])

    def test_partial_cleanup_reports_remaining_objects(self):
        companyname.prepare(self.path, "company001")
        self.cloud.fail_grant_delete = True
        with self.assertRaises(workshop.WorkshopError):
            companyname.cleanup(self.path)
        self.assertIsNone(self.cloud.user)
        self.assertEqual(len(self.cloud.grants), 1)
        self.cloud.fail_grant_delete = False
        self.assertTrue(companyname.cleanup(self.path)["complete"])
        self.assertTrue(companyname.cleanup(self.path)["complete"])

    def test_preexisting_graph_permission_is_preserved(self):
        self.cloud.grants = [{"id": GRANT_ID, "principalId":
                              self.base["outputs"]["managedIdentityPrincipalId"],
                              "resourceId": GRAPH_ID, "appRoleId": APP_ROLE_ID}]
        companyname.prepare(self.path, "company001")
        companyname.cleanup(self.path)
        self.assertEqual(len(self.cloud.grants), 1)

    def test_cleanup_deletes_recorded_grant_despite_stale_collection(self):
        companyname.prepare(self.path, "company001")
        self.cloud.hide_grants = True
        self.assertTrue(companyname.cleanup(self.path)["complete"])
        self.assertEqual(self.cloud.grants, [])

    def test_prepare_waits_for_grant_replication(self):
        original_request = self.cloud.request

        def delayed_grant(method, path, **kwargs):
            result = original_request(method, path, **kwargs)
            if method == "POST" and path.endswith("/appRoleAssignedTo"):
                self.cloud.pending_grant_reads = 1
            return result

        with patch.object(self.cloud, "request", side_effect=delayed_grant):
            with patch("scripts.companyname.sleep"):
                self.assertTrue(companyname.prepare(self.path, "company001")["prepared"])
        companyname.cleanup(self.path)

    def test_unacknowledged_grant_delete_keeps_cleanup_incomplete(self):
        companyname.prepare(self.path, "company001")
        self.cloud.hide_grants = True
        original_request = self.cloud.request

        def invisible_grant(method, path, **kwargs):
            if method == "DELETE" and "/appRoleAssignedTo/" in path:
                if kwargs.get("missing_ok"):
                    return None
                raise workshop.WorkshopError("DELETE API failed: HTTP 404.")
            return original_request(method, path, **kwargs)

        with patch.object(self.cloud, "request", side_effect=invisible_grant):
            with self.assertRaisesRegex(workshop.WorkshopError, "cleanup incomplete"):
                companyname.cleanup(self.path)
        self.assertFalse(self.companion()["grant"]["removed"])
        self.assertEqual(len(self.cloud.grants), 1)

    def test_unknown_grant_after_response_loss_is_not_declared_removed(self):
        self.cloud.lost_grant_response = True
        self.cloud.hide_grants = True
        with patch("scripts.companyname.monotonic", side_effect=[0, 181]):
            with self.assertRaisesRegex(workshop.WorkshopError, "cleanup also failed"):
                companyname.prepare(self.path, "company001")
        report = json.loads((self.path.parent / "abcd1234"
                             / "companyname-cleanup.json").read_text())
        self.assertFalse(report["complete"])
        self.assertEqual(len(self.cloud.grants), 1)
        self.assertFalse(self.companion()["grant"]["removed"])
        self.cloud.hide_grants = False
        self.assertTrue(companyname.cleanup(self.path)["complete"])
        self.assertEqual(self.cloud.grants, [])

    def test_invalid_company_name_never_writes(self):
        for value in ("", "   ", "a" * 65):
            with self.subTest(value=value), self.assertRaises(workshop.WorkshopError):
                companyname.prepare(self.path, value)
        self.assertEqual(self.cloud.writes, [])

    def test_missing_function_reader_blocks_preparation(self):
        self.base["outputs"]["subscriptionReaderAssignmentId"] = ""
        workshop.save_state(self.path, self.base)
        with self.assertRaises(workshop.WorkshopError):
            companyname.prepare(self.path, "company001")
        self.assertEqual(self.cloud.writes, [])

    def test_assignment_survives_until_observed_success(self):
        companyname.prepare(self.path, "company001")
        self.cloud.pending_queries = 1
        with patch("scripts.companyname.sleep", create=True):
            result = companyname.exercise(self.path)
        self.assertTrue(result["verified"])
        self.assertEqual(result["function"]["companyName"], "company001")
        self.assertEqual(self.cloud.query_snapshots, [(True, True), (True, True)])
        self.assertIsNone(self.cloud.user)
        self.assertEqual(self.cloud.grants, [])
        self.assertEqual(self.cloud.roles, self.original_roles)
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_timeout_and_interrupt_cleanup_owned_objects(self):
        for error in (None, KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                self.setUp()
                companyname.prepare(self.path, "company001")
                self.cloud.pending_queries = 100
                self.cloud.query_error = error
                expected = KeyboardInterrupt if error is not None else workshop.WorkshopError
                with patch("scripts.companyname.monotonic", side_effect=[0, 2], create=True):
                    with self.assertRaises(expected):
                        companyname.exercise(self.path, timeout_seconds=1)
                self.assertIsNone(self.cloud.user)
                self.assertEqual(self.cloud.grants, [])
                self.assertEqual(self.cloud.roles, self.original_roles)

    def test_received_log_is_not_enrichment_proof(self):
        companyname.prepare(self.path, "company001")
        self.cloud.row_overrides = {"EventName": "RbacRoleAssignmentReceived"}
        with self.assertRaises(workshop.WorkshopError):
            companyname.exercise(self.path, timeout_seconds=1)
        self.assertIsNone(self.cloud.user)

    def test_wrong_company_principal_role_or_scope_never_verifies(self):
        for key, value in (("CompanyName", "another-company"), ("PrincipalId", GRAPH_ID),
                           ("Scope", "/subscriptions/" + self.base["subscriptionId"]),
                           ("RoleName", "Owner"), ("RoleDefinitionId", GRAPH_ID)):
            with self.subTest(key=key):
                self.setUp()
                companyname.prepare(self.path, "company001")
                self.cloud.row_overrides = {key: value}
                with self.assertRaises(workshop.WorkshopError):
                    companyname.exercise(self.path, timeout_seconds=1)
                self.assertIsNone(self.cloud.user)

    def test_failed_or_mismatched_invocation_never_verifies(self):
        for key, value in (("Success", False), ("RequestInvocationId", "another"),
                           ("RequestOperationId", "another")):
            with self.subTest(key=key):
                self.setUp()
                companyname.prepare(self.path, "company001")
                self.cloud.row_overrides = {key: value}
                with self.assertRaises(workshop.WorkshopError):
                    companyname.exercise(self.path, timeout_seconds=1)

    def test_lost_assignment_response_still_cleans_reserved_role(self):
        companyname.prepare(self.path, "company001")
        self.cloud.lost_role_response = True
        with self.assertRaisesRegex(workshop.WorkshopError, "lost role response"):
            companyname.exercise(self.path)
        self.assertEqual(self.cloud.roles, self.original_roles)
        self.assertIsNone(self.cloud.user)


class OperatorTransportTests(unittest.TestCase):
    def test_operator_token_avoids_conflicting_subscription_and_tenant_options(self):
        state = state_fixture()

        def cli(args):
            self.assertIn("--subscription", args)
            if "--tenant" in args:
                raise workshop.WorkshopError("Specify only one of subscription and tenant")
            return {"tenant": state["tenantId"], "accessToken": "private-token"}

        with patch.object(workshop, "run_az", side_effect=cli):
            with patch("requests.request", return_value=response(body={"id": USER_ID})):
                user = companyname.OperatorClient(state).request("GET", "/v1.0/me")
        self.assertEqual(user["id"], USER_ID)

    def test_role_cast_uses_advanced_query_on_every_page(self):
        state = state_fixture()
        token = {"tenant": state["tenantId"], "accessToken": "private-token"}
        role_path = "/v1.0/me/transitiveMemberOf/microsoft.graph.directoryRole"
        pages = [
            {"id": state["userObjectId"]},
            {"value": [], "@odata.nextLink":
             "https://graph.microsoft.com" + role_path + "?$count=true&$skiptoken=next"},
            {"value": [{"roleTemplateId": GLOBAL_ADMIN}]},
            {"value": [{"id": state["tenantId"], "verifiedDomains": [{
                "name": "workshop.onmicrosoft.com", "isInitial": True, "type": "Managed",
            }]}]},
            {"id": GRAPH_ID, "appId": "00000003-0000-0000-c000-000000000000",
             "appRoles": [{"id": APP_ROLE_ID, "value": "User.Read.All", "isEnabled": True,
                           "allowedMemberTypes": ["Application"]}]},
        ]
        with patch.object(workshop, "scoped", return_value=token):
            with patch("requests.request", side_effect=[
                    response(body=page) for page in pages]) as send:
                ready = companyname._preflight(state, companyname.OperatorClient(state))
        self.assertTrue(ready["ready"])
        calls = [call for call in send.call_args_list if role_path in call.args[1]]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].kwargs["params"].get("$count"), "true")
        for call in calls:
            self.assertEqual(call.kwargs["headers"].get("ConsistencyLevel"), "eventual")

    def test_operator_client_rejects_redirects_without_exposing_response(self):
        state = state_fixture()
        token = {"tenant": state["tenantId"], "accessToken": "private-token"}
        reply = response(302, {"secret": "private-response"},
                         {"Location": "https://example.invalid"})
        with patch.object(workshop, "scoped", return_value=token):
            with patch("requests.request", return_value=reply) as send:
                with self.assertRaises(workshop.WorkshopError) as caught:
                    companyname.OperatorClient(state).request("GET", "/v1.0/me")
        self.assertNotIn("private-response", str(caught.exception))
        self.assertNotIn("private-token", str(caught.exception))
        self.assertIs(send.call_args.kwargs["allow_redirects"], False)

    def test_wrong_token_tenant_and_foreign_pagination_fail(self):
        state = state_fixture()
        for token_tenant, body in (
            (USER_ID, {"id": USER_ID}),
            (state["tenantId"], {"value": [],
                                 "@odata.nextLink": "https://example.invalid/v1.0/users"}),
        ):
            token = {"tenant": token_tenant, "accessToken": "private-token"}
            with self.subTest(tenant=token_tenant):
                with patch.object(workshop, "scoped", return_value=token):
                    with patch("requests.request", return_value=response(body=body)) as send:
                        with self.assertRaises(workshop.WorkshopError):
                            companyname.OperatorClient(state).items("/v1.0/users")
                        for call in send.call_args_list:
                            self.assertTrue(call.args[1].startswith("https://graph.microsoft.com/"))
