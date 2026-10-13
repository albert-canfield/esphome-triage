"""Narrow a regression to the PRs that touched the relevant components between two releases.

Works on a bare, blob-less clone of esphome/esphome: commit and tree history only, so it stays small,
and `git log -- <path>` works offline. Patch releases are cherry-picks with new commit hashes, so PRs are
compared by their "(#1234)" number, not by hash.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess

from .parse import BOILERPLATE_COMPONENTS, Version

UPSTREAM = "https://github.com/esphome/esphome.git"
TAG_RE = re.compile(r"^(20\d\d)\.(\d{1,2})\.(\d{1,2})(b\d+)?$")
PR_RE = re.compile(r"\(#(\d+)\)\s*$")
RELEASE_SUBJECT_RE = re.compile(r"^(Bump version to )?\d{4}\.\d+\.\d+(b\d+|-dev)?\b")
MECHANICAL_RE = re.compile(
    r"clang-tidy|\bbump\b|exception chaining|performance-unnecessary|\btypo|docstring|\bdocument\b|"
    r"\blint|ruff|type hints?|rename .* constant|codeowners|Concatenate nested namespaces",
    re.I,
)
DEPS_RE = re.compile(r"^(DEPENDENCIES|AUTO_LOAD)\s*=\s*\[([^\]]*)\]", re.M)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def ensure_mirror(path: Path, update: bool = True) -> Path:
    """Create or update the history-only clone used for narrowing."""
    if not (path / "HEAD").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"  cloning commit history of esphome/esphome into {path} (one time, no file contents)...")
        subprocess.run(["git", "clone", "--bare", "--filter=blob:none", UPSTREAM, str(path)], check=True)
    elif update:
        git(path, "fetch", "--tags", "--prune", "origin", "+refs/heads/*:refs/heads/*")
    return path


def release_tags(repo: Path) -> list[tuple[Version, str]]:
    out = []
    for tag in git(repo, "tag", "--list", "20*").split():
        if match := TAG_RE.match(tag):
            suffix = match.group(4) or ""
            out.append((Version(int(match.group(1)), int(match.group(2)), int(match.group(3)), suffix), tag))
    return sorted(out, key=lambda vt: (vt[0].year, vt[0].minor, vt[0].patch or 0, vt[0].suffix == "", vt[0].suffix))


def resolve(version: Version, tags: list[tuple[Version, str]], role: str) -> str | None:
    """Map a reported version to a tag. "2026.5.x" means the first 2026.5 release when broken,
    and the last 2026.5 release when it is the working one."""
    stable = [(v, t) for v, t in tags if v.suffix == ""]
    if version.suffix and (exact := [t for v, t in tags if v == version]):
        return exact[0]
    same = [(v, t) for v, t in stable if v.minor_key == version.minor_key]
    if version.patch is not None and (exact := [t for v, t in same if v.patch == version.patch]):
        return exact[0]
    if not same:
        return None
    return same[0][1] if role == "bad" else same[-1][1]


def previous_release(tags: list[tuple[Version, str]], bad_tag: str) -> str | None:
    """Last stable release of the minor series before the broken one."""
    stable = [(v, t) for v, t in tags if v.suffix == ""]
    bad = next(v for v, t in stable if t == bad_tag)
    earlier = [t for v, t in stable if v.minor_key < bad.minor_key]
    return earlier[-1] if earlier else None


def component_paths(repo: Path, ref: str, names: list[str], follow_deps: bool = True) -> list[str]:
    """Component folders, plus the components they depend on or auto-load (one level deep)."""
    paths: list[str] = []
    for name in names:
        path = f"esphome/components/{name}/"
        if path not in paths:
            paths.append(path)
        if not follow_deps:
            continue
        try:
            source = git(repo, "show", f"{ref}:esphome/components/{name}/__init__.py")
        except subprocess.CalledProcessError:
            continue
        for match in DEPS_RE.finditer(source):
            for dep in re.findall(r"[\"']([a-z0-9_]+)[\"']", match.group(2)):
                if dep in BOILERPLATE_COMPONENTS:
                    continue  # esp32, network, ... would pull in half the codebase
                dep_path = f"esphome/components/{dep}/"
                if dep_path not in paths:
                    paths.append(dep_path)
    return paths


@dataclass
class Candidate:
    pr: int | None
    sha: str
    date: str
    subject: str
    paths: list[str]

    @property
    def link(self) -> str:
        return f"#{self.pr}" if self.pr else self.sha[:10]

    @property
    def title(self) -> str:
        return PR_RE.sub("", self.subject).strip()

    @property
    def mechanical(self) -> bool:
        """Refactors, lint fixes and version bumps: possible, but rarely the cause."""
        return bool(MECHANICAL_RE.search(self.subject))


def _changes(repo: Path, rev_range: str, paths: list[str]) -> dict[str, Candidate]:
    log = git(repo, "log", "--no-merges", "--format=%x1e%H%x09%cs%x09%s", "--name-only", rev_range, "--", *paths)
    found: dict[str, Candidate] = {}
    for record in log.split("\x1e"):
        lines = [line for line in record.strip().splitlines() if line.strip()]
        if not lines:
            continue
        sha, date, subject = lines[0].split("\t", 2)
        if RELEASE_SUBJECT_RE.match(subject):
            continue
        match = PR_RE.search(subject)
        key = match.group(1) if match else sha
        touched = sorted({p for p in paths for f in lines[1:] if f.startswith(p)})
        if key not in found:
            found[key] = Candidate(int(match.group(1)) if match else None, sha, date, subject, touched)
    return found


def candidates(repo: Path, good: str, bad: str, paths: list[str]) -> list[Candidate]:
    """PRs that touched `paths` and are in `bad` but not in `good`, oldest first.

    Only history after the two releases split is read: everything before is in both. Fixes that were
    cherry-picked into `good`'s patch releases show up on its side and are subtracted by PR number.
    """
    base = git(repo, "merge-base", good, bad).strip()
    in_bad = _changes(repo, f"{base}..{bad}", paths)
    in_good = _changes(repo, f"{base}..{good}", paths)
    return sorted((c for key, c in in_bad.items() if key not in in_good), key=lambda c: (c.mechanical, c.date))
