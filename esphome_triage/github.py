"""Fetch issues and pull requests with the GitHub CLI and keep them in a local cache."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

MAIN_REPO = "esphome/esphome"
LEGACY_REPO = "esphome/issues"
ISSUE_BODY_LIMIT = 20000
PR_BODY_LIMIT = 1500


@dataclass
class Item:
    """One GitHub issue or pull request, trimmed to what triage needs."""

    repo: str
    number: int
    kind: str  # "issue" or "pr"
    state: str  # "open", "closed" or "merged"
    title: str
    body: str
    labels: list[str]
    created: str
    closed: str | None
    url: str
    author: str
    comments: int = 0
    reactions: int = 0
    state_reason: str | None = None
    draft: bool = False

    @property
    def ref(self) -> str:
        """Short reference as GitHub renders it from inside esphome/esphome."""
        if self.repo == MAIN_REPO:
            return f"#{self.number}"
        return f"{self.repo}#{self.number}"

    @property
    def label_components(self) -> list[str]:
        return [label.split(": ", 1)[1] for label in self.labels if label.startswith("component: ")]


@dataclass
class Cache:
    fetched: str
    items: list[Item] = field(default_factory=list)

    def by_ref(self) -> dict[str, Item]:
        return {item.ref: item for item in self.items}


JQ_ITEM = (
    ".[] | {number, title, body: (.body // \"\"), labels: [.labels[].name], created: .created_at,"
    " closed: .closed_at, url: .html_url, author: (.user.login // \"?\"), comments,"
    " reactions: (.reactions.total_count // 0), state, state_reason, draft: (.draft // false),"
    " pr: (.pull_request != null), merged: (.pull_request.merged_at // null)}"
)


def _gh_paginate(endpoint: str) -> list[dict]:
    result = subprocess.run(
        ["gh", "api", "--paginate", endpoint, "--jq", JQ_ITEM],
        check=True,
        capture_output=True,
        text=True,
    )
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def _to_item(repo: str, raw: dict) -> Item:
    is_pr = raw["pr"]
    if is_pr and raw["merged"]:
        state = "merged"
    else:
        state = raw["state"]
    limit = PR_BODY_LIMIT if is_pr else ISSUE_BODY_LIMIT
    return Item(
        repo=repo,
        number=raw["number"],
        kind="pr" if is_pr else "issue",
        state=state,
        title=raw["title"],
        body=raw["body"][:limit],
        labels=raw["labels"],
        created=raw["created"],
        closed=raw["merged"] or raw["closed"],
        url=raw["url"],
        author=raw["author"],
        comments=raw["comments"],
        reactions=raw["reactions"],
        state_reason=raw["state_reason"],
        draft=raw["draft"],
    )


def refresh(cache_path: Path, closed_since: str, include_legacy: bool = True) -> Cache:
    """Download open and recently closed issues and PRs and write them to the cache."""
    items: list[Item] = []
    sources = [
        (MAIN_REPO, f"repos/{MAIN_REPO}/issues?state=open&per_page=100"),
        (MAIN_REPO, f"repos/{MAIN_REPO}/issues?state=closed&since={closed_since}T00:00:00Z&per_page=100"),
    ]
    if include_legacy:
        sources.append((LEGACY_REPO, f"repos/{LEGACY_REPO}/issues?state=open&per_page=100"))
    for repo, endpoint in sources:
        print(f"  fetching {endpoint.split('?')[0]} ({endpoint.split('state=')[1].split('&')[0]})...")
        items.extend(_to_item(repo, raw) for raw in _gh_paginate(endpoint))
    cache = Cache(fetched=datetime.now(timezone.utc).isoformat(timespec="seconds"), items=items)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"fetched": cache.fetched, "items": [asdict(i) for i in items]}))
    return cache


def load(cache_path: Path) -> Cache:
    if not cache_path.exists():
        raise SystemExit(f"No cache at {cache_path}. Run `python -m esphome_triage refresh` first.")
    data = json.loads(cache_path.read_text())
    return Cache(fetched=data["fetched"], items=[Item(**raw) for raw in data["items"]])
