"""Cross-link every open issue: likely duplicates, maybe-fixed, open PRs on the same code, regression suspects."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

from . import narrow, parse
from .github import MAIN_REPO, Cache, Item
from .similar import Index

FIX_REF_RE = re.compile(r"(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s*:?\s*(?:https://github\.com/esphome/esphome/issues/|esphome/esphome#|#)(\d+)", re.I)
ANY_REF_RE = re.compile(r"(?:github\.com/esphome/esphome/issues/|esphome/esphome#|(?<![\w/])#)(\d{3,6})\b")


@dataclass
class Finding:
    item: Item
    duplicates: list[tuple[float, Item, list[str]]] = field(default_factory=list)
    maybe_fixed: list[tuple[float, Item, list[str]]] = field(default_factory=list)
    open_prs: list[tuple[str, Item]] = field(default_factory=list)
    regression: tuple[str, str, list[str], list[narrow.Candidate]] | None = None

    @property
    def interesting(self) -> bool:
        return bool(self.duplicates or self.maybe_fixed or self.open_prs or (self.regression and self.regression[3]))


CRASH_TITLE_RE = re.compile(r"^(ESP32|ESP8266|RP2040)(-[A-Z0-9]+)?: |CRASH DETECTED|^Crash( report)?:", re.I)


def is_crash_report(item: Item) -> bool:
    return bool(CRASH_TITLE_RE.search(item.title)) or "Crash detected in the Device Builder" in item.body


def known_components(repo_checkout: Path | None, mirror: Path | None) -> set[str]:
    if repo_checkout and (repo_checkout / "esphome/components").is_dir():
        return {p.name for p in (repo_checkout / "esphome/components").iterdir() if p.is_dir() and not p.name.startswith("_")}
    if mirror:
        listing = narrow.git(mirror, "ls-tree", "--name-only", "HEAD", "esphome/components/")
        return {line.rsplit("/", 1)[-1] for line in listing.split()}
    raise SystemExit("Need either --repo (an esphome checkout) or a mirror to know component names.")


def pr_links(cache: Cache) -> tuple[dict[int, list[Item]], dict[int, list[Item]]]:
    """Issue number -> PRs that say they fix it, and -> PRs that mention it at all."""
    fixes: dict[int, list[Item]] = {}
    mentions: dict[int, list[Item]] = {}
    for pr in cache.items:
        if pr.kind != "pr" or pr.repo != MAIN_REPO:
            continue
        text = pr.title + "\n" + pr.body
        for num in {int(n) for n in FIX_REF_RE.findall(text)}:
            fixes.setdefault(num, []).append(pr)
        for num in {int(n) for n in ANY_REF_RE.findall(text)}:
            mentions.setdefault(num, []).append(pr)
    return fixes, mentions


def build(cache: Cache, index: Index, comps: set[str], mirror: Path | None, dup_threshold: float, fix_threshold: float) -> list[Finding]:
    fixes, mentions = pr_links(cache)
    open_prs_by_comp: dict[str, list[Item]] = {}
    for pr in cache.items:
        if pr.kind == "pr" and pr.state == "open":
            for comp in pr.label_components + parse.components(pr.title, "", [], comps)[:1]:
                open_prs_by_comp.setdefault(comp, []).append(pr)
    tags = narrow.release_tags(mirror) if mirror else []

    findings = []
    for item in cache.items:
        if item.kind != "issue" or item.state != "open" or item.repo != MAIN_REPO:
            continue
        found = Finding(item)
        doc = index.doc(item)
        for score, other, reasons in index.related(item, limit=25, min_score=min(dup_threshold, fix_threshold)):
            o = other.item
            same_frame = any(r.startswith("same crash frame") for r in reasons)
            if is_crash_report(item) and is_crash_report(o) and not same_frame:
                continue  # generic crash titles say nothing about the cause
            if o.kind == "issue" and o.state == "open" and score >= dup_threshold:
                found.duplicates.append((score, o, reasons))
            elif o.kind == "issue" and o.state == "closed" and o.state_reason == "completed" and score >= fix_threshold and (o.closed or "") > item.created:
                found.maybe_fixed.append((score, o, reasons))
            elif o.kind == "pr" and o.state == "merged" and score >= fix_threshold + 0.1 and (o.closed or "") > item.created:
                found.maybe_fixed.append((score, o, reasons))
        seen: set[int] = set()
        for pr in fixes.get(item.number, []):
            if pr.state == "open" and pr.number not in seen:
                found.open_prs.append(("says it fixes this", pr))
                seen.add(pr.number)
        for pr in mentions.get(item.number, []):
            if pr.state == "open" and pr.number not in seen:
                found.open_prs.append(("mentions this issue", pr))
                seen.add(pr.number)
        for comp in doc.components[:1]:
            for pr in open_prs_by_comp.get(comp, []):
                if pr.number not in seen and not pr.draft:
                    found.open_prs.append((f"open PR on {comp}", pr))
                    seen.add(pr.number)
        if mirror and tags:
            found.regression = regression_for(item, comps, mirror, tags, doc.components)
        findings.append(found)
    return findings


def regression_for(item: Item, comps: set[str], mirror: Path, tags, issue_comps: list[str]):
    good_v, bad_v = parse.good_bad_versions(item.title, item.body)
    if not bad_v or not issue_comps:
        return None
    bad = narrow.resolve(bad_v, tags, "bad")
    good = narrow.resolve(good_v, tags, "good") if good_v else (narrow.previous_release(tags, bad) if bad else None)
    if not bad or not good or good == bad:
        return None
    primary = [c for c in issue_comps if c not in parse.BOILERPLATE_COMPONENTS][:2] or issue_comps[:1]
    paths = narrow.component_paths(mirror, bad, primary)
    try:
        found = narrow.candidates(mirror, good, bad, paths)
    except Exception:  # unknown tag or path in an old release
        return None
    return (good, bad, paths, found)


def _line(score: float, other: Item, reasons: list[str]) -> str:
    why = f" ({'; '.join(reasons)})" if reasons else ""
    state = other.state if other.kind == "issue" else f"PR {other.state}"
    return f"  - {other.ref} [{state}, {score:.2f}] {other.title[:90]}{why}"


def markdown(findings: list[Finding], fetched: str) -> str:
    out = [f"# ESPHome triage report\n\nData fetched {fetched}. Scores are similarity hints, not proof: read both issues before saying anything.\n"]
    groups = [
        ("Likely duplicates (open)", lambda f: f.duplicates, _line),
        ("May already be fixed (similar closed issue or merged PR after this was opened)", lambda f: f.maybe_fixed, _line),
    ]
    for title, pick, fmt in groups:
        rows = [f for f in findings if pick(f)]
        out.append(f"\n## {title} ({len(rows)})\n")
        for f in sorted(rows, key=lambda f: -pick(f)[0][0]):
            out.append(f"- **{f.item.ref}** {f.item.title[:100]}")
            out.extend(fmt(*entry) for entry in pick(f)[:4])
    rows = [f for f in findings if f.open_prs]
    out.append(f"\n## Open PRs on the same code ({len(rows)})\n")
    for f in rows:
        out.append(f"- **{f.item.ref}** {f.item.title[:100]}")
        out.extend(f"  - PR {pr.ref} ({why}) {pr.title[:90]}" for why, pr in f.open_prs[:5])
    rows = [f for f in findings if f.regression and f.regression[3]]
    out.append(f"\n## Regression suspects ({len(rows)})\n")
    for f in sorted(rows, key=lambda f: len(f.regression[3])):
        good, bad, paths, cands = f.regression
        short = ", ".join(p.split("/")[-2] for p in paths)
        out.append(f"- **{f.item.ref}** {f.item.title[:90]}\n  - {good} → {bad}, {len(cands)} change(s) in {short}")
        out.extend(f"  - {c.date} {c.link} {c.subject[:100]}" for c in cands[:8])
    return "\n".join(out) + "\n"
