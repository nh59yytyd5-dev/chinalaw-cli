"""Agent-facing assets should stay aligned with the CLI contract."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from chinalaw import applicability, loader, service
from chinalaw.datapaths import builtin_data_dir

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"
INSTALL_SKILLS_SCRIPT = REPO_ROOT / "scripts" / "install-skills"
INSTALL_SKILLS_PS1 = REPO_ROOT / "scripts" / "install-skills.ps1"


class BuiltinDataPathTests(unittest.TestCase):
    def test_builtin_data_dirs_exist_in_source_tree(self):
        self.assertEqual(builtin_data_dir("fixtures"), loader.FIXTURES_DIR)
        self.assertEqual(
            builtin_data_dir("applicability"),
            applicability.DEFAULT_APPLICABILITY_DIR,
        )
        self.assertTrue(loader.FIXTURES_DIR.exists())
        self.assertTrue(applicability.DEFAULT_APPLICABILITY_DIR.exists())

    def test_pyproject_packages_applicability_data(self):
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("/data/applicability", pyproject)
        self.assertIn('"data/applicability" = "chinalaw/data/applicability"', pyproject)


class SkillTemplateTests(unittest.TestCase):
    def _read_skill(self, name: str) -> str:
        path = SKILLS_DIR / name / "SKILL.md"
        self.assertTrue(path.exists(), f"missing skill template: {path}")
        return path.read_text(encoding="utf-8")

    def test_legal_research_skill_mentions_required_cli_flow(self):
        text = self._read_skill("legal-research")
        for token in (
            "chinalaw search",
            "chinalaw article",
            "chinalaw fetch",
            "chinalaw applicable",
            "chinalaw relation",
            "chinalaw norm ingest",
            "chinalaw pack validate",
            "article: null",
            "needs_fetch",
            "pending_reference_in_pack",
            "not_legal_conclusion",
        ):
            self.assertIn(token, text)
        self.assertIn("不得凭模型记忆", text)

    def test_contract_review_skill_mentions_current_time_effect_disciplines(self):
        text = self._read_skill("contract-review")
        for token in (
            "chinalaw applicable",
            "chinalaw relation",
            "chinalaw fetch",
            "norm clause",
            "not_legal_conclusion",
            "needs_fetch",
            "law_missing",
            "law_stub",
            "pending_reference_in_pack",
        ):
            self.assertIn(token, text)
        self.assertIn("不得凭模型记忆", text)

    def test_skill_readme_lists_templates(self):
        text = (SKILLS_DIR / "README.md").read_text(encoding="utf-8")
        self.assertIn("legal-research/SKILL.md", text)
        self.assertIn("contract-review/SKILL.md", text)

    def test_chinalaw_using_resolve_examples_are_fixture_backed(self):
        text = self._read_skill("chinalaw-using")
        examples = [
            name
            for name in re.findall(r"^chinalaw resolve\s+([^\s#<]+)", text, flags=re.MULTILINE)
        ]
        self.assertGreaterEqual(len(examples), 1)

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "t.db"
            loader.load_fixtures(db_path, loader.FIXTURES_DIR)
            for name in examples:
                with self.subTest(name=name):
                    payload = service.resolve(db_path, name)
                    self.assertTrue(payload["matched"], f"{name} is not backed by builtin fixtures")

    def test_contract_documents_resolve_command_schema(self):
        text = (REPO_ROOT / "docs" / "CONTRACT.md").read_text(encoding="utf-8")
        self.assertIn("### 4.1.1 `resolve <name>`", text)
        for token in (
            '"matched": "boolean"',
            "alias_derived",
            "like_fallback",
            "未命中返回 `1`",
        ):
            self.assertIn(token, text)

    def test_skill_files_have_yaml_frontmatter(self):
        # SKILL.md must start with YAML frontmatter (name + description),
        # otherwise agents cannot lazy-load by description and have to read
        # the entire file. This is the de-facto standard followed by Claude
        # Code, OpenCode, Codex CLI, Cursor, and superpowers.
        for skill_name in ("legal-research", "contract-review"):
            path = SKILLS_DIR / skill_name / "SKILL.md"
            text = path.read_text(encoding="utf-8")
            self.assertTrue(
                text.startswith("---\n"),
                f"{path} must start with YAML frontmatter (---)",
            )
            parts = text.split("---", 2)
            self.assertGreaterEqual(
                len(parts),
                3,
                f"{path} frontmatter not closed",
            )
            frontmatter = parts[1]
            self.assertIn(f"name: {skill_name}", frontmatter)
            self.assertIn("description:", frontmatter)


class SkillInstallScriptTests(unittest.TestCase):
    def _run_installer(self, *args: str) -> subprocess.CompletedProcess[str]:
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is not available")
        return subprocess.run(
            [bash, str(INSTALL_SKILLS_SCRIPT), *args],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_windows_installer_script_exists(self):
        self.assertTrue(INSTALL_SKILLS_PS1.exists())

    def test_copy_install_marks_and_uninstalls_only_managed_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skills"

            self._run_installer("--copy", "--target", str(target))

            for name in ("legal-research", "contract-review"):
                installed = target / name
                self.assertTrue(installed.exists())
                marker = installed / ".chinalaw-cli-install"
                self.assertTrue(marker.exists())
                self.assertIn("managed_by=chinalaw-cli", marker.read_text())

            self._run_installer("--uninstall", "--target", str(target))

            self.assertFalse((target / "legal-research").exists())
            self.assertFalse((target / "contract-review").exists())

    def test_uninstall_skips_user_authored_skill_with_same_frontmatter_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skills"
            user_skill = target / "legal-research"
            user_skill.mkdir(parents=True)
            (user_skill / "SKILL.md").write_text(
                "---\nname: legal-research\ndescription: user skill\n---\n",
                encoding="utf-8",
            )

            result = self._run_installer("--uninstall", "--target", str(target))

            self.assertTrue(user_skill.exists())
            self.assertTrue((user_skill / "SKILL.md").exists())
            self.assertIn("skip (foreign dir)", result.stdout)


if __name__ == "__main__":
    unittest.main()
