import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = PROJECT_ROOT / "distribution" / "OpenCode-CFD-Agent"
SKILLS = PACKAGE / ".agents" / "skills"


class CodexCompatibilityTests(unittest.TestCase):
    def test_codex_skill_metadata_is_distributed(self):
        expected = {
            "cfd-case-agent": "$cfd-case-agent",
            "stl-geometry-analyzer": "$stl-geometry-analyzer",
        }
        for skill_name, invocation in expected.items():
            skill_dir = SKILLS / skill_name
            metadata = skill_dir / "agents" / "openai.yaml"
            self.assertTrue((skill_dir / "SKILL.md").is_file(), skill_name)
            self.assertTrue(metadata.is_file(), skill_name)
            text = metadata.read_text(encoding="utf-8")
            self.assertIn("display_name:", text)
            self.assertIn("short_description:", text)
            self.assertIn(f'default_prompt: "Use {invocation}', text)
            self.assertNotIn("allow_implicit_invocation: false", text)

    def test_agent_rules_cover_both_supported_hosts(self):
        rules = " ".join(
            (PACKAGE / "AGENTS.md").read_text(encoding="utf-8").split()
        )
        self.assertIn("Codex or", rules)
        self.assertIn("OpenCode", rules)
        self.assertIn("only the controller may cross into WSL", rules)

    def test_codex_guide_is_present(self):
        guide = (PROJECT_ROOT / "CODEX手順.md").read_text(encoding="utf-8")
        self.assertIn("$cfd-case-agent", guide)
        self.assertIn("$stl-geometry-analyzer", guide)
        self.assertIn("Codexデスクトップアプリ", guide)
        self.assertIn("codex", guide)


if __name__ == "__main__":
    unittest.main()
