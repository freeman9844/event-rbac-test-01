"""Cloud Shell workshop support; never changes the default Azure subscription."""

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID


class WorkshopError(RuntimeError):
    pass


def run_az(args: list[str]) -> object:
    executable = shutil.which("az")
    if not executable:
        raise WorkshopError("Azure CLI is missing. Install it or use Cloud Shell Bash.")
    try:
        result = subprocess.run(
            [executable, *args, "--output", "json", "--only-show-errors"],
            capture_output=True, text=True, encoding="utf-8", timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorkshopError(f"Azure CLI failed: {error}") from error
    if result.returncode:
        raise WorkshopError(f"Azure CLI exit {result.returncode}: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise WorkshopError("Azure CLI did not return valid JSON.") from error


def require(condition: bool, message: str) -> None:
    if not condition:
        raise WorkshopError(message)


def uuid_value(value: object) -> str:
    require(isinstance(value, str), "Expected a UUID string.")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise WorkshopError("Invalid UUID.") from error
    require(str(parsed) == value.lower(), "Expected a canonical UUID.")
    return str(parsed)


def utc_time(value: object) -> datetime:
    require(isinstance(value, str), "Expected an explicit UTC timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise WorkshopError("Invalid UTC timestamp.") from error
    require(parsed.utcoffset() == timezone.utc.utcoffset(parsed), "Timestamp must be UTC.")
    return parsed


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_state(state: object) -> dict:
    keys = {
        "schemaVersion", "runId", "subscriptionId", "tenantId", "userObjectId",
        "resourceGroupName", "namePrefix", "location", "createdAtUtc",
        "deploymentNames", "outputs", "assignment",
    }
    require(isinstance(state, dict) and set(state) == keys, "Unexpected state fields.")
    require(type(state["schemaVersion"]) is int and state["schemaVersion"] == 1,
            "Unsupported state version; do not reconstruct an old run.")
    run_id = state["runId"]
    require(isinstance(run_id, str) and re.fullmatch(r"[a-f0-9]{8}", run_id) is not None,
            "Invalid run ID.")
    for key in ("subscriptionId", "tenantId", "userObjectId"):
        uuid_value(state[key])
    require(state["resourceGroupName"] == f"rg-rbac-workshop-{run_id}", "Foreign group.")
    require(state["namePrefix"] == "rbac-lab", "Unexpected name prefix.")
    require(isinstance(state["location"], str)
            and re.fullmatch(r"[a-z][a-z0-9]{1,39}", state["location"]) is not None,
            "Invalid location.")
    require(state["deploymentNames"] == {
        "infra": f"rbac-ws-{run_id}-infra", "events": f"rbac-ws-{run_id}-events",
    }, "Foreign deployment names.")
    utc_time(state["createdAtUtc"])
    require(isinstance(state["outputs"], dict), "Invalid deployment outputs.")
    if state["outputs"]:
        validate_outputs(state, state["outputs"])
    require(state["assignment"] is None or isinstance(state["assignment"], dict),
            "Invalid assignment record.")
    return state


def validate_outputs(state: dict, outputs: object) -> dict:
    keys = {"functionAppName", "functionAppResourceId", "managedIdentityPrincipalId",
            "applicationInsightsName", "workspaceResourceId", "systemTopicName",
            "subscriptionReaderAssignmentId"}
    require(isinstance(outputs, dict) and set(outputs) == keys
            and all(isinstance(value, str) for value in outputs.values()),
            "Unexpected deployment outputs.")
    match = re.fullmatch(r"func-rbac-lab-([a-z0-9]{13})", outputs["functionAppName"])
    require(match is not None, "Unexpected function name.")
    suffix = match.group(1)
    scope = group_scope(state)
    expected = {
        "functionAppResourceId": scope + "/providers/Microsoft.Web/sites/"
        + outputs["functionAppName"],
        "workspaceResourceId": scope + "/providers/Microsoft.OperationalInsights/workspaces/"
        + f"log-rbac-lab-{suffix}",
        "applicationInsightsName": f"appi-rbac-lab-{suffix}",
        "systemTopicName": f"evgt-rbac-lab-{suffix}",
    }
    for key, value in expected.items():
        require(outputs[key].lower() == value.lower(), f"Foreign output: {key}")
    uuid_value(outputs["managedIdentityPrincipalId"])
    reader = outputs["subscriptionReaderAssignmentId"]
    if reader:
        prefix = (f"/subscriptions/{state['subscriptionId']}/providers/"
                  "Microsoft.Authorization/roleAssignments/")
        require(reader.lower().startswith(prefix.lower()), "Foreign subscription Reader.")
        uuid_value(reader[len(prefix):])
    return outputs


def load_state(path: Path) -> dict:
    try:
        return validate_state(json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkshopError(f"Cannot read state {path}: {error}") from error


def save_state(path: Path, state: dict) -> None:
    validate_state(state)
    require(not path.is_symlink(), "Refusing a symbolic-link state file.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream, indent=2)
            stream.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def scoped(state: dict, args: list[str]) -> object:
    return run_az([*args, "--subscription", state["subscriptionId"]])


def group_scope(state: dict) -> str:
    return (f"/subscriptions/{state['subscriptionId']}/resourceGroups/"
            f"{state['resourceGroupName']}")


def ownership_tags(state: dict) -> dict:
    return {"workshop": "rbac-event", "runId": state["runId"], "managedBy": "workshop"}


def check_identity(state: dict) -> str:
    account = scoped(state, ["account", "show"])
    require(isinstance(account, dict)
            and account.get("id", "").lower() == state["subscriptionId"].lower()
            and account.get("tenantId", "").lower() == state["tenantId"].lower()
            and account.get("user", {}).get("type") == "user", "Wrong account or tenant.")
    user = scoped(state, ["ad", "signed-in-user", "show"])
    require(isinstance(user, dict), "Invalid signed-in user response.")
    return uuid_value(user.get("id"))


def owned_group(state: dict) -> bool:
    exists = scoped(state, ["group", "exists", "--name", state["resourceGroupName"]])
    require(type(exists) is bool, "Cannot determine whether the group exists.")
    if not exists:
        return False
    group = scoped(state, ["group", "show", "--name", state["resourceGroupName"]])
    require(isinstance(group, dict)
            and group.get("id", "").lower() == group_scope(state).lower(),
            "Unexpected resource group response.")
    tags = group.get("tags")
    require(isinstance(tags, dict)
            and all(tags.get(k) == v for k, v in ownership_tags(state).items()),
            "Resource group ownership mismatch. Nothing will be deleted.")
    return True


def check_run(state: dict) -> None:
    validate_state(state)
    require(check_identity(state) == state["userObjectId"].lower(), "Wrong signed-in user.")
    owned_group(state)


def init_run(root: Path, subscription_id: str, tenant_id: str, location: str) -> Path:
    run_id = secrets.token_hex(4)
    state = {
        "schemaVersion": 1, "runId": run_id,
        "subscriptionId": uuid_value(subscription_id), "tenantId": uuid_value(tenant_id),
        "userObjectId": "00000000-0000-0000-0000-000000000000",
        "resourceGroupName": f"rg-rbac-workshop-{run_id}", "namePrefix": "rbac-lab",
        "location": location, "createdAtUtc": now_utc(),
        "deploymentNames": {
            "infra": f"rbac-ws-{run_id}-infra", "events": f"rbac-ws-{run_id}-events",
        },
        "outputs": {}, "assignment": None,
    }
    validate_state(state)
    state["userObjectId"] = check_identity(state)
    source = f"/subscriptions/{state['subscriptionId']}"
    endpoint = source + "/providers/Microsoft.EventGrid/systemTopics"
    url = f"https://management.azure.com{endpoint}?api-version=2025-02-15"
    visited = set()
    while url:
        parsed = urlparse(url)
        require(parsed.scheme == "https" and parsed.netloc == "management.azure.com"
                and parsed.path.lower() == endpoint.lower() and url not in visited,
                "Unsafe or repeated system-topic pagination URL.")
        visited.add(url)
        page = scoped(state, ["rest", "--method", "get", "--url", url])
        require(isinstance(page, dict) and isinstance(page.get("value"), list),
                "Cannot list system topics.")
        for topic in page["value"]:
            require(isinstance(topic, dict) and isinstance(topic.get("properties"), dict),
                    "Malformed system topic.")
            topic_source = topic["properties"].get("source")
            require(isinstance(topic_source, str), "Missing system-topic source.")
            require(topic_source.lower() != source.lower(),
                    "A system topic already monitors this subscription. Use a separate "
                    "lab subscription; do not change or delete the existing topic.")
        url = page.get("nextLink")
        require(url is None or isinstance(url, str), "Invalid pagination URL.")
    require(not owned_group(state), "New run group already exists.")
    path = root / ".workshop-state" / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            os.chmod(path, 0o600)
            json.dump(state, stream, indent=2)
            stream.write("\n")
    except FileExistsError as error:
        raise WorkshopError("Run state already exists; refusing to overwrite it.") from error
    return path


def arm_get(state: dict, resource_id: str, version: str) -> dict:
    value = scoped(state, ["rest", "--method", "get", "--url",
                          f"https://management.azure.com{resource_id}?api-version={version}"])
    require(isinstance(value, dict), "Invalid ARM resource response.")
    return value


def capture_deployment(state_file: Path, deployment_name: str) -> None:
    state = load_state(state_file)
    require(deployment_name in state["deploymentNames"].values(), "Foreign deployment name.")
    check_run(state)
    require(owned_group(state), "Deployment group is missing.")
    deployment = scoped(state, ["deployment", "sub", "show", "--name", deployment_name])
    require(isinstance(deployment, dict)
            and deployment.get("properties", {}).get("provisioningState") == "Succeeded",
            "Deployment has not succeeded; inspect it before continuing.")
    raw_outputs = deployment["properties"].get("outputs")
    require(isinstance(raw_outputs, dict)
            and all(isinstance(item, dict) and "value" in item
                    for item in raw_outputs.values()), "Missing deployment outputs.")
    outputs = validate_outputs(state, {key: item["value"] for key, item in raw_outputs.items()})
    app = arm_get(state, outputs["functionAppResourceId"], "2024-11-01")
    require(app.get("id", "").lower() == outputs["functionAppResourceId"].lower()
            and app.get("identity", {}).get("principalId", "").lower()
            == outputs["managedIdentityPrincipalId"].lower(),
            "Deployment managed identity does not belong to this function.")
    state["outputs"] = outputs
    save_state(state_file, state)


def package_sources(root: Path, destination: Path) -> list[str]:
    root = root.resolve()
    module = root / "rbac_monitor"
    require(module.is_dir() and not module.is_symlink(), "Missing or symbolic source package.")
    paths = [root / name for name in ("function_app.py", "host.json", "requirements.txt")]
    for path in module.rglob("*"):
        require(not path.is_symlink(), "Refusing symbolic package contents.")
        if path.suffix == ".py" and path.is_file():
            paths.append(path)
    require((module / "__init__.py") in paths and (module / "events.py") in paths,
            "Receiver Python sources are missing.")
    for path in paths:
        require(path.is_file() and not path.is_symlink()
                and path.resolve().is_relative_to(root), "Unsafe or missing package source.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    names = []
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            name = path.relative_to(root).as_posix()
            archive.write(path, name)
            names.append(name)
    return names


def main() -> None:
    parser = argparse.ArgumentParser(description="RBAC event workshop")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("show", "check", "capture", "package"):
        command = commands.add_parser(name)
        command.add_argument("state", type=Path)
        if name == "capture":
            command.add_argument("--deployment", required=True)
    init = commands.add_parser("init")
    init.add_argument("--subscription", required=True)
    init.add_argument("--tenant", required=True)
    init.add_argument("--location", default="koreacentral")
    args = parser.parse_args()
    try:
        if args.command == "init":
            print(init_run(Path.cwd(), args.subscription, args.tenant, args.location))
        else:
            state = load_state(args.state)
            if args.command == "check":
                check_run(state)
                print("PASS: run identity and ownership checked.")
            elif args.command == "capture":
                capture_deployment(args.state, args.deployment)
                print("PASS: deployment outputs captured.")
            elif args.command == "package":
                destination = args.state.parent / state["runId"] / "receiver.zip"
                package_sources(Path(__file__).resolve().parents[1], destination)
                print(destination)
            else:
                print(json.dumps(state, ensure_ascii=False))
    except (WorkshopError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
