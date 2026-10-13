"""Find related issues and PRs with TF-IDF over the parts of a report that carry signal."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math
import re

from . import parse
from .github import Item

STOPWORDS = set(
    """
    the and for with that this when not but are was were have has had from after before then than into
    out about there their they them its it's you your our can could would should will just also only
    some any all one two get got does did doing done use used using work works working worked same
    other more most very still yet like what which who how why where while because been being over
    under again here then esphome issue problem version installation platform component config yaml
    logs log home assistant addon add-on docker pip response additional information anything useful
    causing type what which using esp32 esp8266 device devices please thanks thank hello hi see seems
    seem time times now new old way something nothing everything maybe need needs want try tried
    """.split()
)
TOKEN_RE = re.compile(r"[a-z][a-z0-9_]{2,}")


def tokens(text: str) -> list[str]:
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS]


@dataclass
class Doc:
    item: Item
    components: list[str]
    errors: list[str]
    frames: list[str]
    vector: dict[str, float]


def signal_text(item: Item, comps: list[str], errors: list[str], frames: list[str]) -> str:
    """Title and description, weighted towards the parts that identify a bug. YAML and raw logs are left out."""
    if item.kind == "issue" and "### " in item.body:
        description = parse.section(item.body, "the problem") + "\n" + parse.section(item.body, "additional information")
    else:
        description = item.body[:1500]
    parts = [item.title] * 3 + [description] + [" ".join(comps)] * 2 + errors * 2 + [" ".join(frames)] * 2
    return "\n".join(parts)


class Index:
    """Inverted TF-IDF index so scoring one item against thousands stays fast."""

    def __init__(self, items: list[Item], known_components: set[str]) -> None:
        self.docs: list[Doc] = []
        raw: list[Counter[str]] = []
        for item in items:
            comps = parse.components(item.title, item.body, item.labels, known_components)
            errors = parse.error_lines(item.body) if item.kind == "issue" else []
            frames = parse.crash_frames(item.body) if item.kind == "issue" else []
            raw.append(Counter(tokens(signal_text(item, comps, errors, frames))))
            self.docs.append(Doc(item, comps, errors, frames, {}))
        df: Counter[str] = Counter()
        for counts in raw:
            df.update(counts.keys())
        error_df: Counter[str] = Counter()
        for doc in self.docs:
            error_df.update(set(doc.errors))
        common = max(5, len(self.docs) // 500)
        self.common_errors = {line for line, n in error_df.items() if n > common}
        n = len(raw)
        self.postings: dict[str, list[tuple[int, float]]] = defaultdict(list)
        for idx, counts in enumerate(raw):
            vec = {t: (1 + math.log(c)) * math.log((n + 1) / (df[t] + 1)) for t, c in counts.items()}
            norm = math.sqrt(sum(w * w for w in vec.values())) or 1.0
            vec = {t: w / norm for t, w in vec.items()}
            self.docs[idx].vector = vec
            for term, weight in vec.items():
                self.postings[term].append((idx, weight))
        self.position = {(d.item.repo, d.item.number): i for i, d in enumerate(self.docs)}

    def doc(self, item: Item) -> Doc:
        return self.docs[self.position[(item.repo, item.number)]]

    def related(self, item: Item, limit: int = 10, min_score: float = 0.25) -> list[tuple[float, Doc, list[str]]]:
        """Most similar items, each with a short list of reasons a person can check."""
        target = self.doc(item)
        dots: dict[int, float] = defaultdict(float)
        for term, weight in target.vector.items():
            for idx, other_weight in self.postings[term]:
                dots[idx] += weight * other_weight
        scored = []
        for idx, cosine in dots.items():
            other = self.docs[idx]
            if other.item is item:
                continue
            score, reasons = cosine, []
            shared_comps = [c for c in target.components[:2] if c in other.components[:3]]
            if shared_comps:
                score += 0.12
                reasons.append("same component: " + ", ".join(shared_comps))
            shared_errors = (set(target.errors) & set(other.errors)) - self.common_errors
            if shared_errors:
                score += 0.25
                reasons.append("same log line: " + sorted(shared_errors, key=len)[-1][:90])
            shared_frames = [f for f in target.frames if f in other.frames]
            if shared_frames:
                score += 0.3
                reasons.append("same crash frame: " + ", ".join(shared_frames[:3]))
            if score >= min_score:
                scored.append((round(score, 3), other, reasons))
        scored.sort(key=lambda s: -s[0])
        return scored[:limit]
