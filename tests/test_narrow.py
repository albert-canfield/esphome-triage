from pathlib import Path
import subprocess
import tempfile
import unittest

from esphome_triage import narrow
from esphome_triage.parse import Version


def run(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def commit(repo: Path, path: str, content: str, subject: str) -> None:
    file = repo / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(content)
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", subject)


class NarrowTest(unittest.TestCase):
    """Builds a tiny history: dev with three PRs, a release branch with a cherry-picked patch."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        repo = self.repo = Path(self.tmp.name)
        run(repo, "init", "-q", "-b", "dev")
        run(repo, "config", "user.email", "t@example.com")
        run(repo, "config", "user.name", "t")
        commit(repo, "esphome/components/es8388/__init__.py", 'DEPENDENCIES = ["i2c"]\nAUTO_LOAD = ["audio_dac"]\n', "[es8388] Add component (#100)")
        commit(repo, "esphome/components/i2c/i2c.cpp", "v1", "[i2c] Initial (#101)")
        run(repo, "tag", "2026.4.0")
        run(repo, "checkout", "-q", "-b", "release")
        commit(repo, "esphome/components/i2c/fix.cpp", "fix", "[i2c] Fix NACK handling (#105)")
        run(repo, "tag", "2026.4.1")
        run(repo, "checkout", "-q", "dev")
        commit(repo, "esphome/components/i2c/fix.cpp", "fix", "[i2c] Fix NACK handling (#105)")
        commit(repo, "esphome/components/audio_dac/dac.cpp", "volume", "[audio_dac] Optimize volume (#110)")
        commit(repo, "esphome/components/wifi/wifi.cpp", "x", "[wifi] Unrelated (#111)")
        commit(repo, "esphome/const.py", "2026.5.0", "2026.5.0 (#112)")
        run(repo, "tag", "2026.5.0")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_dependencies_are_followed(self) -> None:
        paths = narrow.component_paths(self.repo, "2026.5.0", ["es8388"])
        self.assertEqual(paths, ["esphome/components/es8388/", "esphome/components/i2c/", "esphome/components/audio_dac/"])

    def test_cherry_picks_and_unrelated_changes_are_excluded(self) -> None:
        paths = narrow.component_paths(self.repo, "2026.5.0", ["es8388"])
        found = narrow.candidates(self.repo, "2026.4.1", "2026.5.0", paths)
        self.assertEqual([c.pr for c in found], [110])
        self.assertEqual(found[0].paths, ["esphome/components/audio_dac/"])

    def test_resolve_versions(self) -> None:
        tags = narrow.release_tags(self.repo)
        self.assertEqual(narrow.resolve(Version(2026, 5, None), tags, "bad"), "2026.5.0")
        self.assertEqual(narrow.resolve(Version(2026, 4, None), tags, "good"), "2026.4.1")
        self.assertEqual(narrow.previous_release(tags, "2026.5.0"), "2026.4.1")


if __name__ == "__main__":
    unittest.main()
