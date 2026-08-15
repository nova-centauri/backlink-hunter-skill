import json
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "backlink-hunter"
SKILL_MD = SKILL_ROOT / "SKILL.md"


class SkillContractTest(unittest.TestCase):
    def test_frontmatter_matches_portable_agent_skills_contract(self):
        content = SKILL_MD.read_text(encoding="utf-8")
        match = re.match(r"\A---\n(.*?)\n---\n", content, re.DOTALL)
        self.assertIsNotNone(match, "SKILL.md must start with YAML frontmatter")
        lines = match.group(1).splitlines()
        self.assertEqual(len(lines), 2, "Portable frontmatter should contain only name and description")
        self.assertEqual(lines[0], "name: backlink-hunter")
        self.assertTrue(lines[1].startswith('description: "') and lines[1].endswith('"'))
        description = json.loads(lines[1].removeprefix("description: "))
        self.assertTrue(1 <= len(description) <= 1024)
        self.assertNotIn("<", description)
        self.assertNotIn(">", description)
        self.assertEqual(SKILL_ROOT.name, "backlink-hunter")

    def test_main_skill_stays_within_progressive_disclosure_limit(self):
        self.assertLessEqual(len(SKILL_MD.read_text(encoding="utf-8").splitlines()), 500)

    def test_every_linked_reference_exists_one_level_below_skill(self):
        content = SKILL_MD.read_text(encoding="utf-8")
        linked = set(re.findall(r"\]\((references/[^)]+)\)", content))
        self.assertTrue(linked)
        for relative in linked:
            self.assertEqual(len(Path(relative).parts), 2)
            self.assertTrue((SKILL_ROOT / relative).is_file(), relative)

    def test_openai_metadata_invokes_the_portable_skill_name(self):
        metadata = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn('display_name: "Backlink Hunter"', metadata)
        self.assertIn("$backlink-hunter", metadata)

    def test_skill_directory_has_no_human_readme(self):
        self.assertFalse((SKILL_ROOT / "README.md").exists())


if __name__ == "__main__":
    unittest.main()
