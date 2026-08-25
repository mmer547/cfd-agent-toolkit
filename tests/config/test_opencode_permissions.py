import fnmatch
import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG = PROJECT_ROOT / "distribution" / "OpenCode-CFD-Agent" / "opencode.json"
AGENT_RULES = PROJECT_ROOT / "distribution" / "OpenCode-CFD-Agent" / "AGENTS.md"
CFD_SKILL = PROJECT_ROOT / "distribution" / "OpenCode-CFD-Agent" / ".agents" / "skills" / "cfd-case-agent" / "SKILL.md"
RERUN_COMMAND = PROJECT_ROOT / "distribution" / "OpenCode-CFD-Agent" / ".opencode" / "commands" / "cfd-rerun.md"
COMMAND_DIR = PROJECT_ROOT / "distribution" / "OpenCode-CFD-Agent" / ".opencode" / "commands"


class OpenCodePermissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.permissions = json.loads(CONFIG.read_text(encoding="utf-8"))["permission"]
        cls.rules = cls.permissions["bash"]

    def decision(self, command):
        result = None
        for pattern, action in self.rules.items():
            if fnmatch.fnmatchcase(command, pattern):
                result = action
        return result

    def test_read_only_controller_commands_are_allowed(self):
        prefix = "python .agents/skills/cfd-case-agent/scripts/cfdctl.py --case . "
        self.assertEqual(self.decision(prefix + "inspect"), "allow")
        self.assertEqual(self.decision(prefix + "plan mesh"), "allow")
        self.assertEqual(self.decision(prefix + "plan rerun"), "allow")
        self.assertEqual(self.decision(prefix + "mesh-optimization-status --run-id test"), "allow")
        shared = "python .agents/skills/cfd-case-agent/scripts/cfdctl.py --workspace . "
        self.assertEqual(self.decision(shared + "discover-cases --root sample"), "allow")
        self.assertEqual(self.decision(shared + "--case sample/pitzDaily plan mesh"), "allow")
        self.assertEqual(
            self.decision(shared + "batch-plan solve sample/pitzDaily sample/damBreak"),
            "allow",
        )

    def test_real_cfd_execution_still_asks_once(self):
        prefix = "python .agents/skills/cfd-case-agent/scripts/cfdctl.py --case . "
        self.assertEqual(self.decision(prefix + "run-command openfoam.blockMesh --execute"), "ask")
        self.assertEqual(self.decision(prefix + "run-workflow mesh --execute"), "ask")
        self.assertEqual(self.decision(prefix + "run-workflow rerun --execute"), "ask")
        self.assertEqual(self.decision(prefix + "optimize-mesh --execute"), "ask")
        self.assertEqual(self.decision(prefix + "optimize-mesh --max-cells 2500 --execute"), "ask")
        self.assertEqual(self.decision(prefix + "recover-mesh-optimization run-1"), "ask")
        shared = "python .agents/skills/cfd-case-agent/scripts/cfdctl.py --workspace . "
        self.assertEqual(
            self.decision(shared + "--case sample/pitzDaily run-workflow solve --execute"),
            "ask",
        )
        self.assertEqual(
            self.decision(shared + "batch-run solve sample/pitzDaily sample/damBreak --execute"),
            "ask",
        )

    def test_stl_analyzer_is_allowed(self):
        command = (
            "python .agents/skills/stl-geometry-analyzer/scripts/analyze_stl.py "
            "constant/triSurface --units m --output .cfd-runs/geometry/stl-analysis.json"
        )
        self.assertEqual(self.decision(command), "allow")

    def test_direct_cfd_and_wsl_commands_remain_denied(self):
        self.assertEqual(self.decision("blockMesh"), "deny")
        self.assertEqual(self.decision("mpirun -np 4 foamRun -parallel"), "deny")
        self.assertEqual(self.decision("wsl.exe -d Ubuntu"), "deny")

    def test_unregistered_shell_command_still_asks(self):
        self.assertEqual(self.decision("python unrelated.py"), "ask")

    def test_workspace_file_tools_do_not_ask(self):
        for tool in ("read", "edit", "glob", "grep", "list"):
            self.assertEqual(self.permissions[tool], "allow")

    def test_external_data_access_is_denied(self):
        self.assertEqual(self.permissions["external_directory"], "deny")
        self.assertEqual(self.permissions["webfetch"], "deny")
        self.assertEqual(self.permissions["websearch"], "deny")
        self.assertEqual(self.permissions["*"], "ask")

    def test_progress_reporting_is_required_by_agent_and_skill(self):
        agent_rules = " ".join(AGENT_RULES.read_text(encoding="utf-8").split())
        skill = " ".join(CFD_SKILL.read_text(encoding="utf-8").split())
        self.assertIn("Before every controller invocation", agent_rules)
        self.assertIn("After it returns", agent_rules)
        self.assertIn("host_locale", agent_rules)
        self.assertIn("## Data boundary", agent_rules)
        self.assertIn("Never read or write a path outside the workspace root", agent_rules)
        self.assertIn("## Report progress", skill)
        self.assertIn("Before each controller call", skill)
        self.assertIn("host_locale", skill)

    def test_rerun_slash_command_is_distributed(self):
        command = RERUN_COMMAND.read_text(encoding="utf-8")
        self.assertIn("plan rerun", command)
        self.assertIn("run-workflow rerun --execute", command)
        self.assertIn("constant/polyMesh", command)

    def test_shared_workspace_slash_commands_are_distributed(self):
        for name in ("cfd-cases.md", "cfd-batch-preview.md", "cfd-batch-run.md"):
            self.assertTrue((COMMAND_DIR / name).is_file(), name)
        self.assertIn("--workspace .", (COMMAND_DIR / "cfd-inspect.md").read_text(encoding="utf-8"))
        self.assertIn("batch-plan", (COMMAND_DIR / "cfd-batch-preview.md").read_text(encoding="utf-8"))
        self.assertIn("batch-run", (COMMAND_DIR / "cfd-batch-run.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
