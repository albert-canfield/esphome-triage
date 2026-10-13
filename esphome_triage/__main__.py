"""Command line: refresh | related | narrow | overlap | report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import github, narrow, parse, report
from .similar import Index

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "cache.json"
MIRROR = DATA / "esphome.git"
DEFAULT_CHECKOUT = ROOT.parent / "esphome"


def _components(args) -> set[str]:
    return report.known_components(Path(args.repo) if args.repo else None, MIRROR if (MIRROR / "HEAD").exists() else None)


def _find(cache: github.Cache, ref: str) -> github.Item:
    ref = ref if ref.startswith("#") or "/" in ref else f"#{ref}"
    for item in cache.items:
        if item.ref == ref and item.kind == "issue":
            return item
    raise SystemExit(f"{ref} is not in the cache (only open issues and issues closed since the refresh date are).")


def cmd_refresh(args) -> None:
    print("Refreshing GitHub data...")
    cache = github.refresh(CACHE, args.since, include_legacy=not args.no_legacy)
    counts: dict[str, int] = {}
    for item in cache.items:
        key = f"{item.repo} {item.kind} {item.state}"
        counts[key] = counts.get(key, 0) + 1
    for key, n in sorted(counts.items()):
        print(f"  {n:6} {key}")
    if not args.no_mirror:
        print("Updating history mirror...")
        narrow.ensure_mirror(MIRROR)
    print("Done.")


def cmd_related(args) -> None:
    cache = github.load(CACHE)
    item = _find(cache, args.issue)
    index = Index(cache.items, _components(args))
    doc = index.doc(item)
    print(f"{item.ref} {item.title}\n  components: {', '.join(doc.components) or '?'}")
    if doc.frames:
        print(f"  crash frames: {', '.join(doc.frames[:5])}")
    for score, other, reasons in index.related(item, limit=args.limit, min_score=args.min_score):
        o = other.item
        state = o.state if o.kind == "issue" else f"PR {o.state}{' draft' if o.draft else ''}"
        print(f"\n  {score:.2f}  {o.ref} [{state}] {o.title[:100]}\n        {o.url}")
        for reason in reasons:
            print(f"        - {reason}")


def cmd_narrow(args) -> None:
    mirror = narrow.ensure_mirror(MIRROR, update=not args.offline)
    tags = narrow.release_tags(mirror)
    comps = list(args.component or [])
    good_v = parse.Version.parse(args.good) if args.good else None
    bad_v = parse.Version.parse(args.bad) if args.bad else None
    if args.issue:
        cache = github.load(CACHE)
        item = _find(cache, args.issue)
        known = _components(args)
        guess_good, guess_bad = parse.good_bad_versions(item.title, item.body)
        good_v, bad_v = good_v or guess_good, bad_v or guess_bad
        if not comps:
            comps = [c for c in parse.components(item.title, item.body, item.labels, known) if c not in parse.BOILERPLATE_COMPONENTS][:2]
        extra = [c for c in parse.config_components(item.body, known) if c not in comps]
        if args.with_config:
            comps += extra
            extra = []
        print(f"{item.ref} {item.title}")
    if not bad_v or not comps:
        raise SystemExit("Need a broken version and at least one component (from the issue, or --bad and -c).")
    bad = narrow.resolve(bad_v, tags, "bad")
    good = narrow.resolve(good_v, tags, "good") if good_v else (narrow.previous_release(tags, bad) if bad else None)
    if not bad or not good:
        raise SystemExit(f"Could not map versions to release tags (good={good_v}, bad={bad_v}).")
    paths = narrow.component_paths(mirror, bad, comps, follow_deps=not args.no_deps)
    if args.core:
        paths.append("esphome/core/")
    found = narrow.candidates(mirror, good, bad, paths)
    print(f"  worked: {good}   broken: {bad}   (guessed from the report)" if args.issue and not (args.good and args.bad) else f"  worked: {good}   broken: {bad}")
    print(f"  watching: {', '.join(paths)}")
    real = [c for c in found if not c.mechanical]
    print(f"  {len(found)} change(s) landed in between, {len(real)} of them not just clean-up:\n")
    for c in found:
        where = ", ".join(p.split("/")[-2] for p in c.paths)
        mark = "  (clean-up)" if c.mechanical else ""
        print(f"  {c.date}  {c.link:>7}  {c.title[:95]}  · {where}{mark}")
    if args.issue and extra:
        print(f"\n  The reporter's YAML also uses: {', '.join(extra)}. Add --with-config to watch them too.")
    if found:
        prs = [str(c.pr) for c in found if c.pr]
        pool = real or found
        mid = sorted(pool, key=lambda c: c.date)[len(pool) // 2]
        print(f"\n  To bisect on a device, flash the middle candidate first and ask: good or bad?\n"
              f"    uvx --from git+https://github.com/esphome/esphome@{mid.sha[:12]} esphome run device.yaml\n"
              f"  PR numbers: {' '.join(prs)}")


def cmd_overlap(args) -> None:
    cache = github.load(CACHE)
    known = _components(args)
    comps = list(args.component or [])
    if args.issue:
        item = _find(cache, args.issue)
        comps += parse.components(item.title, item.body, item.labels, known)[:2]
    if not comps:
        raise SystemExit("Give an issue or -c component.")
    print(f"Open PRs touching: {', '.join(comps)}\n")
    for pr in cache.items:
        if pr.kind != "pr" or pr.state != "open":
            continue
        hit = set(comps) & (set(pr.label_components) | set(parse.components(pr.title, "", [], known)[:1]))
        if hit:
            print(f"  {pr.ref:>7} {'[draft] ' if pr.draft else ''}{pr.title[:100]}  ({pr.author})\n          {pr.url}")


def cmd_report(args) -> None:
    cache = github.load(CACHE)
    known = _components(args)
    index = Index(cache.items, known)
    mirror = MIRROR if (MIRROR / "HEAD").exists() and not args.no_narrow else None
    findings = report.build(cache, index, known, mirror, args.dup, args.fix)
    out = Path(args.out)
    out.write_text(report.markdown(findings, cache.fetched))
    if args.json:
        Path(args.json).write_text(json.dumps([
            {
                "issue": f.item.number,
                "duplicates": [{"ref": o.ref, "score": s, "why": r} for s, o, r in f.duplicates],
                "maybe_fixed": [{"ref": o.ref, "kind": o.kind, "score": s, "why": r} for s, o, r in f.maybe_fixed],
                "open_prs": [{"ref": p.ref, "why": w} for w, p in f.open_prs],
                "regression": None if not f.regression else {
                    "good": f.regression[0], "bad": f.regression[1],
                    "candidates": [{"pr": c.pr, "date": c.date, "subject": c.subject} for c in f.regression[3]],
                },
            }
            for f in findings if f.interesting
        ], indent=1))
    print(f"Wrote {out} ({sum(f.interesting for f in findings)} of {len(findings)} open issues have a lead).")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="esphome_triage", description=__doc__)
    parser.add_argument("--repo", default=str(DEFAULT_CHECKOUT) if DEFAULT_CHECKOUT.is_dir() else None,
                        help="esphome checkout used for component names (default: ../esphome)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("refresh", help="download open + recently closed issues and PRs")
    p.add_argument("--since", default="2026-01-01", help="also fetch items closed since this date")
    p.add_argument("--no-legacy", action="store_true", help="skip the old esphome/issues tracker")
    p.add_argument("--no-mirror", action="store_true", help="skip cloning/updating the history mirror")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("related", help="issues and PRs that look like the same problem")
    p.add_argument("issue")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--min-score", type=float, default=0.2)
    p.set_defaults(func=cmd_related)

    p = sub.add_parser("narrow", help="PRs that touched the component between the working and broken release")
    p.add_argument("issue", nargs="?")
    p.add_argument("--good", help="last working version, e.g. 2026.4.5")
    p.add_argument("--bad", help="first broken version, e.g. 2026.5.0")
    p.add_argument("-c", "--component", action="append", help="component to watch (repeatable)")
    p.add_argument("--with-config", action="store_true", help="also watch the non-boilerplate components in the YAML")
    p.add_argument("--core", action="store_true", help="also watch esphome/core/")
    p.add_argument("--no-deps", action="store_true", help="don't follow DEPENDENCIES/AUTO_LOAD")
    p.add_argument("--offline", action="store_true", help="don't fetch new tags")
    p.set_defaults(func=cmd_narrow)

    p = sub.add_parser("overlap", help="open PRs on the same component (check before starting work)")
    p.add_argument("issue", nargs="?")
    p.add_argument("-c", "--component", action="append")
    p.set_defaults(func=cmd_overlap)

    p = sub.add_parser("report", help="cross-link all open issues into a markdown report")
    p.add_argument("--out", default="triage-report.md")
    p.add_argument("--json")
    p.add_argument("--dup", type=float, default=0.55, help="score needed to flag an open duplicate")
    p.add_argument("--fix", type=float, default=0.5, help="score needed to flag a maybe-fixed match")
    p.add_argument("--no-narrow", action="store_true")
    p.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
