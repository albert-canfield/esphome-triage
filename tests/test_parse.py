import unittest

from esphome_triage import parse
from esphome_triage.parse import Version

KNOWN = {"es8388", "i2s_audio", "speaker", "wifi", "modbus", "modbus_controller", "online_image", "esp32", "uart", "sensor"}

TEMPLATE = """### The problem

Speaker volume is very low since 2026.5.x. With 2026.4.5 it works correctly, I downgraded to check.

### Which version of ESPHome has the issue?

2026.5.2

### What type of installation are you using?

Home Assistant Add-on

### Component causing the issue

es8388, i2s_audio

### YAML Config

```yaml
esphome:
  name: kitchen
wifi:
  ssid: x
i2s_audio:
  - id: bus
speaker:
  - platform: i2s_audio
modbus:
```

### Anything in the logs that might be useful for us?

[12:00:01][E][es8388:120]: Failed to write register 0x1A
[12:00:02][W][component:443]: es8388 took a long time for an operation (82 ms)

### Additional information

_No response_
"""


class SectionTest(unittest.TestCase):
    def test_sections_split_on_headings(self) -> None:
        self.assertEqual(parse.section(TEMPLATE, "which version"), "2026.5.2")
        self.assertEqual(parse.section(TEMPLATE, "additional information"), "")

    def test_missing_section_is_empty(self) -> None:
        self.assertEqual(parse.section("plain text", "the problem"), "")


class VersionTest(unittest.TestCase):
    def test_parse_variants(self) -> None:
        self.assertEqual(Version.parse("2026.5.2"), Version(2026, 5, 2))
        self.assertEqual(Version.parse("since 2026.5.x"), Version(2026, 5, None))
        self.assertEqual(Version.parse("2026.10.0b3"), Version(2026, 10, 0, "b3"))
        self.assertIsNone(Version.parse("no version here"))

    def test_good_and_bad_from_report(self) -> None:
        good, bad = parse.good_bad_versions("es8388 low volume", TEMPLATE)
        self.assertEqual(good, Version(2026, 4, 5))
        self.assertEqual(bad, Version(2026, 5, None))

    def test_good_newer_than_bad_is_dropped(self) -> None:
        body = "### The problem\n\nWorks fine in 2026.9.0\n\n### Which version of ESPHome has the issue?\n\n2026.8.1\n"
        good, bad = parse.good_bad_versions("x", body)
        self.assertIsNone(good)
        self.assertEqual(bad, Version(2026, 8, 1))


    def test_mistyped_version_field_loses_to_the_text(self) -> None:
        body = ("### The problem\n\nWorks with 2026.4.5. Starting with 2026.5.3 the speaker is quiet, still in 2026.8.2.\n\n"
                "### Which version of ESPHome has the issue?\n\n2025.8.2\n")
        self.assertEqual(parse.good_bad_versions("low volume", body), (Version(2026, 4, 5), Version(2026, 5, 3)))


class ComponentTest(unittest.TestCase):
    def test_field_first_and_boilerplate_last(self) -> None:
        comps = parse.components("[es8388] low volume on esp32", TEMPLATE, [], KNOWN)
        self.assertEqual(comps, ["es8388", "i2s_audio"])  # "esp32" in a title is the platform, not the culprit

    def test_labels_win(self) -> None:
        self.assertEqual(parse.components("x", "", ["component: modbus"], KNOWN), ["modbus"])

    def test_config_components_skip_boilerplate(self) -> None:
        self.assertEqual(parse.config_components(TEMPLATE, KNOWN), ["i2s_audio", "speaker", "modbus"])


class SignalTest(unittest.TestCase):
    def test_error_lines_are_normalized(self) -> None:
        errors = parse.error_lines(TEMPLATE)
        self.assertIn("[E][es8388] Failed to write register 0x#", errors)
        self.assertTrue(any("took a long time" in e and "(# ms)" in e for e in errors))

    def test_crash_frames_drop_generic(self) -> None:
        body = (
            "0x4037d4c2: esp_cpu_wait_for_intr at cpu.c:64\n"
            "0x420e1be1: esp_vApplicationIdleHook at freertos_hooks.c:58\n"
            "0x42011234: esphome::online_image::OnlineImage::loop() at online_image.cpp:120\n"
        )
        self.assertEqual(parse.crash_frames(body), ["esphome::online_image::OnlineImage::loop"])


if __name__ == "__main__":
    unittest.main()
