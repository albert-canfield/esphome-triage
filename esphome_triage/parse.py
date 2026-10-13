"""Pull structured facts out of ESPHome issue text: sections, versions, components, errors, crash frames."""

from __future__ import annotations

from dataclasses import dataclass
from functools import total_ordering
import re

SECTION_RE = re.compile(r"^###\s*(.+?)\s*$", re.M)
VERSION_RE = re.compile(r"(?<![\d.])(20\d\d)\.(\d{1,2})(?:\.(\d{1,2}|x|X))?(b\d+|-dev)?(?![\d])")
TITLE_TAG_RE = re.compile(r"^\s*\[([a-z0-9_]+)\]")
FRAME_RE = re.compile(r"0x[0-9a-fA-F]{6,8}:\s+([A-Za-z_][\w:~]*)(?:\([^)]*\))?\s+(?:at|in)\b")
LOG_LINE_RE = re.compile(r"\[([EW])\]\[([\w.]+)(?::\d+)?\]:\s*(.+)")
COMPILE_ERROR_RE = re.compile(r"(?:error|fatal error):\s*(.+)", re.I)

NO_RESPONSE = "_No response_"

GOOD_WORDS = re.compile(
    r"\b(work(?:s|ed|ing)?|fine|okay|ok|stable|good|downgrad\w*|revert\w*|roll(?:ed|ing)? back|"
    r"last working|until|before|prior to|previous)\b",
    re.I,
)
BAD_WORDS = re.compile(
    r"\b(since|after|broke\w*|broken|stop(?:ped|s)?|fail\w*|regress\w*|upgrad\w*|updat\w*|"
    r"not working|doesn'?t work|no longer|starting with|introduced)\b",
    re.I,
)

# Frames that show up in almost every crash and say nothing about the cause.
GENERIC_FRAMES = {
    "esp_vApplicationIdleHook", "prvIdleTask", "esp_cpu_wait_for_intr", "xt_utils_wait_for_intr",
    "vPortTaskWrapper", "panic_abort", "esp_system_abort", "abort", "__assert_func", "panic_handler",
    "xt_unhandled_exception", "_xt_lowint1", "esp_restart_noos", "vTaskDelay", "vPortYield",
    "xQueueReceive", "xQueueGenericReceive", "xQueueSemaphoreTake", "ulTaskGenericNotifyTake", "main_task",
    "app_main", "loopTask", "loop", "setup", "esp_task_wdt_isr", "task_wdt_isr", "_xt_panic",
    "esp_crosscore_isr", "panic_print_backtrace", "esp_panic_handler", "frame_to_panic_info",
    "print_backtrace", "esp_backtrace_print", "vTaskSwitchContext", "_frxt_dispatch", "xPortStartScheduler",
    "esphome::Application::loop", "esphome::Scheduler::call", "call_loop_", "std::function",
    "prvCheckTasksWaitingTermination", "esp_libc_include_assert_impl", "task_wdt_timeout_handling",
    "esphome::Application::scheduler_tick_", "esphome::loop_task", "esphome::Application::feed_wdt",
    "esp_cpu_stall", "esp_ipc_isr_stall_other_cpu", "vTaskDelete", "xTaskResumeAll", "vPortExitCritical",
    "vPortEnterCritical", "spinlock_acquire", "spinlock_release", "_xt_coproc_exc", "xt_utils_get_core_id",
}

# Log lines that describe the crash handler or boot environment rather than the bug.
NOISE_LOG_RE = re.compile(
    r"^\[[EW]\]\[(esp32\.crash|esp8266\.crash|rp2040\.crash|crash_handler|logger|app)\]|^error: (false|true)$",
)

# Components that appear in nearly every config; they only matter when named as the culprit.
BOILERPLATE_COMPONENTS = {
    "esphome", "esp32", "esp8266", "rp2040", "bk72xx", "rtl87xx", "ln882x", "libretiny", "host", "wifi", "api",
    "ota", "logger", "captive_portal", "web_server", "mdns", "network", "safe_mode", "preferences",
    "substitutions", "packages", "psram", "time", "sntp", "homeassistant", "debug", "external_components",
    "globals", "script", "interval", "binary_sensor", "sensor", "text_sensor", "switch", "button", "output",
    "light", "number", "select", "text", "status_led", "restart", "uptime", "wifi_signal", "wifi_info",
    "version", "template", "improv_serial", "esp32_improv", "improv_ble", "dashboard_import",
}


def sections(body: str) -> dict[str, str]:
    """Split a templated issue body into its `### Heading` sections."""
    out: dict[str, str] = {}
    matches = list(SECTION_RE.finditer(body or ""))
    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        text = body[match.end():end].strip()
        out[match.group(1).strip().lower()] = "" if text == NO_RESPONSE else text
    return out


def section(body: str, *names: str) -> str:
    found = sections(body)
    for name in names:
        for heading, text in found.items():
            if heading.startswith(name.lower()):
                return text
    return ""


@total_ordering
@dataclass(frozen=True)
class Version:
    year: int
    minor: int
    patch: int | None  # None when the reporter wrote "2026.5" or "2026.5.x"
    suffix: str = ""

    @classmethod
    def parse(cls, text: str) -> Version | None:
        match = VERSION_RE.search(text)
        if not match:
            return None
        patch = match.group(3)
        return cls(
            int(match.group(1)),
            int(match.group(2)),
            int(patch) if patch and patch.isdigit() else None,
            match.group(4) or "",
        )

    @property
    def minor_key(self) -> tuple[int, int]:
        return (self.year, self.minor)

    def sort_key(self) -> tuple[int, int, int, int, str]:
        """"2026.5.x" sorts before every 2026.5 patch, dev builds before betas before the release."""
        stage = 0 if self.suffix == "-dev" else 1 if self.suffix.startswith("b") else 2
        return (self.year, self.minor, -1 if self.patch is None else self.patch, stage, self.suffix)

    def __lt__(self, other: Version) -> bool:
        return self.sort_key() < other.sort_key()

    def __str__(self) -> str:
        patch = "x" if self.patch is None else str(self.patch)
        return f"{self.year}.{self.minor}.{patch}{self.suffix}"


def version_mentions(text: str) -> list[tuple[Version, int]]:
    out = []
    for match in VERSION_RE.finditer(text or ""):
        version = Version.parse(match.group(0))
        if version and 1 <= version.minor <= 12:
            out.append((version, match.start()))
    return out


# Dots inside a version are followed by a digit, so "2026.4.5. Next" still ends a sentence.
SENTENCE_END_RE = re.compile(r"[.!?;](?=\s)|\n|,\s+(?:but|and|while|whereas)\b")


def _sentence_around(text: str, pos: int) -> str:
    """The clause containing `pos`, so hints from a neighbouring sentence don't leak in."""
    start = 0
    for match in SENTENCE_END_RE.finditer(text, 0, pos):
        start = match.end()
    end_match = SENTENCE_END_RE.search(text, pos)
    return text[start: end_match.start() if end_match else len(text)]


def good_bad_versions(title: str, body: str) -> tuple[Version | None, Version | None]:
    """Guess the last working and first broken version from a bug report.

    The version field is the broken one. A working version is a mention near words like
    "worked", "fine" or "downgrade" that is older than the broken one.
    """
    field_version = Version.parse(section(body, "which version"))
    text = f"{title}\n{section(body, 'the problem', 'additional information') or body}"
    good: Version | None = None
    broken: list[Version] = []
    for version, pos in version_mentions(text):
        window = _sentence_around(text, pos)
        good_score = len(GOOD_WORDS.findall(window))
        bad_score = len(BAD_WORDS.findall(window))
        if good_score > bad_score:
            if good is None or version > good:
                good = version
        elif bad_score > good_score:
            broken.append(version)
    # The earliest broken version that is newer than the working one. The version field is only a
    # candidate when it agrees with the text: reporters often mistype it (2025.8.2 for 2026.8.2).
    candidates = [v for v in broken if good is None or v > good]
    if field_version and (good is None or field_version > good):
        candidates.append(field_version)
    bad = min(candidates) if candidates else field_version
    if good and bad and good >= bad:
        good = None
    return good, bad


# Names people use for a component that differ from its folder (old names, spelling).
ALIASES = {"dallas": "dallas_temp", "onewire": "one_wire", "ds18b20": "dallas_temp", "waveshare": "waveshare_epaper"}


def components(title: str, body: str, labels: list[str], known: set[str]) -> list[str]:
    """Components the report is about: labels, title tag, the template field, then the title."""
    found: list[str] = []

    def add(name: str) -> None:
        name = name.strip().lower().replace("-", "_")
        name = ALIASES.get(name, name)
        if name in known and name not in found:
            found.append(name)

    for label in labels:
        if label.startswith("component: "):
            add(label.split(": ", 1)[1])
    if match := TITLE_TAG_RE.match(title):
        add(match.group(1))
    field_text = section(body, "component causing the issue").lower().replace("-", "_")
    for word in re.findall(r"[a-z0-9_]{3,}", field_text):
        add(word)
    for word in re.findall(r"[a-z0-9_]{3,}", title.lower()):
        if word not in BOILERPLATE_COMPONENTS:
            add(word)
    specific = [c for c in found if c not in BOILERPLATE_COMPONENTS]
    return specific + [c for c in found if c in BOILERPLATE_COMPONENTS]


def config_components(body: str, known: set[str], include_boilerplate: bool = False) -> list[str]:
    """Components used in the YAML config section: top-level keys and `platform:` values."""
    yaml_text = section(body, "yaml config") or body
    names = re.findall(r"^([a-z0-9_]+):", yaml_text, re.M)
    names += re.findall(r"platform:\s*([a-z0-9_]+)", yaml_text)
    out: list[str] = []
    for name in names:
        if name in known and name not in out and (include_boilerplate or name not in BOILERPLATE_COMPONENTS):
            out.append(name)
    return out


def crash_frames(body: str) -> list[str]:
    """Function names from a decoded backtrace, without the generic ones."""
    frames: list[str] = []
    for match in FRAME_RE.finditer(body or ""):
        name = match.group(1)
        if name not in GENERIC_FRAMES and name not in frames:
            frames.append(name)
    return frames


def _normalize(text: str) -> str:
    text = re.sub(r"\b0x[0-9a-fA-F]+\b", "\x00", text)
    text = re.sub(r"\b\d+\b", "#", text)
    text = text.replace("\x00", "0x#")
    return re.sub(r"\s+", " ", text).strip()[:160]


def error_lines(body: str) -> list[str]:
    """Error and warning log lines plus compiler errors, normalized so they compare across reports."""
    out: list[str] = []
    for line in (body or "").splitlines():
        if match := LOG_LINE_RE.search(line):
            text = f"[{match.group(1)}][{match.group(2)}] {_normalize(match.group(3))}"
        elif match := COMPILE_ERROR_RE.search(line):
            text = "error: " + _normalize(match.group(1))
        else:
            continue
        if 12 <= len(text) and text not in out and not NOISE_LOG_RE.search(text):
            out.append(text)
    return out
