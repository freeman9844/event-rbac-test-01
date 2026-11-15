import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = [
    "README.md", "docs/00-azure-rbac-basics.md", "docs/01-prerequisites.md",
    "docs/02-private-infrastructure.md", "docs/03-function-deployment.md",
    "docs/04-event-grid.md", "docs/05-rbac-event-validation.md",
    "docs/06-cleanup-production.md", "docs/development.md",
]
BASH = (r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else shutil.which("bash"))
CONTRACTS = {"create-group", "infra-preview", "infra-create",
             "publish", "events-preview", "events-create"}
BOOTSTRAP = r"""
if command -v cygpath >/dev/null 2>&1; then AZ_LOG=$(cygpath -u "$AZ_LOG"); fi
SUB_ID=11111111-1111-4111-8111-111111111111
RG=rg-rbac-workshop-abcd1234
LOCATION=koreacentral
PREFIX=rbac-lab
RUN_ID=abcd1234
INFRA_DEPLOYMENT=rbac-ws-abcd1234-infra
EVENTS_DEPLOYMENT=rbac-ws-abcd1234-events
STATE_FILE=.workshop-state/abcd1234.json
restore_workshop() { [[ "${DOC_CHECK_FAIL:-0}" == 0 ]]; }
check_published_receiver() { restore_workshop; }
state_value() {
  case "$1" in
    outputs.functionAppName) printf '%s\n' func-rbac-lab-aaaaaaaaaaaaa ;;
    *) return 1 ;;
  esac
}
python3() {
  if [[ " $* " == *" package "* ]]; then
    printf '%s\n' .workshop-state/abcd1234/receiver.zip
  fi
}
az() {
  printf '%s\0' "$@" __AZ_END__ >> "$AZ_LOG"
  if [[ "$1 $2" == "group exists" ]]; then
    printf '%s\n' "${DOC_GROUP_EXISTS:-false}"
  else
    printf '{}\n'
  fi
}
"""


def contracts():
    result = {}
    for name in DOCUMENTS:
        path = ROOT / name
        if path.is_file():
            for label, script in re.findall(
                r"<!-- command-contract: ([a-z-]+) -->\s*```bash\n(.*?)\n```",
                path.read_text(encoding="utf-8"), re.DOTALL,
            ):
                if label in result:
                    raise AssertionError(f"Duplicate command contract: {label}")
                result[label] = script
    return result


class WorkshopDocTests(unittest.TestCase):
    def test_workshop_navigation_resolves_and_code_fences_close(self):
        for name in DOCUMENTS:
            with self.subTest(document=name):
                path = ROOT / name
                self.assertTrue(path.is_file(), f"Missing workshop module: {name}")
                text = path.read_text(encoding="utf-8")
                fences = re.findall(r"^```[^\n]*$", text, re.MULTILINE)
                self.assertEqual(len(fences) % 2, 0, f"Unclosed code fence in {name}")
                for link in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
                    target = urlsplit(link)
                    if target.scheme or not target.path:
                        continue
                    resolved = (path.parent / unquote(target.path)).resolve()
                    self.assertTrue(resolved.is_relative_to(ROOT))
                    self.assertTrue(resolved.is_file(), f"{name}: broken link {link}")

    def test_all_cloud_shell_blocks_parse_without_running_them(self):
        self.assertTrue(BASH and Path(BASH).is_file() if os.name == "nt" else BASH)
        total = 0
        for name in DOCUMENTS:
            path = ROOT / name
            self.assertTrue(path.is_file(), f"Missing workshop module: {name}")
            for index, script in enumerate(re.findall(
                r"^```bash\n(.*?)^```\s*$", path.read_text(encoding="utf-8"),
                re.MULTILINE | re.DOTALL,
            )):
                with self.subTest(document=name, block=index):
                    parsed = subprocess.run([BASH, "--noprofile", "--norc", "-n"],
                                            input=script, text=True, capture_output=True)
                    self.assertEqual(parsed.returncode, 0, parsed.stderr)
                total += 1
        self.assertGreater(total, 10)

    def run_contract(self, script, **overrides):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / ".workshop-state" / "abcd1234" / "receiver.zip"
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b"placeholder for argument validation")
            log = root / "azure-arguments.bin"
            log.touch()
            env = dict(os.environ, AZ_LOG=str(log), **overrides)
            result = subprocess.run(
                [BASH, "--noprofile", "--norc", "-s"], input=BOOTSTRAP + "\n" + script,
                cwd=root, env=env, text=True, capture_output=True,
            )
            parts = log.read_bytes().decode("utf-8").split("\0")
            calls, current = [], []
            for part in parts:
                if part == "__AZ_END__":
                    calls.append(current)
                    current = []
                elif part:
                    current.append(part)
        return result, calls

    def test_real_document_commands_preserve_isolation_and_linux_build(self):
        scripts = contracts()
        self.assertEqual(set(scripts), CONTRACTS)
        for label, script in scripts.items():
            with self.subTest(contract=label):
                result, calls = self.run_contract(script)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(calls)
                for args in calls:
                    self.assertIn("--subscription", args)
                    self.assertEqual(args[args.index("--subscription") + 1],
                                     "11111111-1111-4111-8111-111111111111")
                    if args[:2] == ["group", "create"]:
                        self.assertEqual(args[args.index("--name") + 1], "rg-rbac-workshop-abcd1234")
                        for tag in ("workshop=rbac-event", "runId=abcd1234", "managedBy=workshop"):
                            self.assertIn(tag, args)
                    elif args[:2] == ["deployment", "sub"]:
                        for parameter in ("assignSubscriptionReader=false", "createResourceGroup=false",
                                          "resourceGroupName=rg-rbac-workshop-abcd1234"):
                            self.assertIn(parameter, args)
                        self.assertIn("enableEventSubscription="
                                      + ("true" if label.startswith("events-") else "false"), args)
                    elif args[:4] == ["functionapp", "deployment", "source", "config-zip"]:
                        self.assertEqual(args[args.index("--build-remote") + 1], "true")
                        self.assertEqual(args[args.index("--src") + 1],
                                         ".workshop-state/abcd1234/receiver.zip")

    def test_failed_preflight_prevents_all_documented_cloud_mutations(self):
        scripts = contracts()
        self.assertEqual(set(scripts), CONTRACTS)
        for label, script in scripts.items():
            with self.subTest(contract=label):
                result, calls = self.run_contract(script, DOC_CHECK_FAIL="1")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])

    def test_group_creation_refuses_existing_or_indeterminate_group(self):
        scripts = contracts()
        self.assertIn("create-group", scripts)
        for existing in ("true", "unexpected-output"):
            result, calls = self.run_contract(scripts["create-group"], DOC_GROUP_EXISTS=existing)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(any(args[:2] == ["group", "create"] for args in calls))
