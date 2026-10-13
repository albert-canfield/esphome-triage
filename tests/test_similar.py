import unittest

from esphome_triage.github import Item
from esphome_triage.similar import Index

KNOWN = {"modbus", "wifi", "online_image", "lvgl"}


def issue(number: int, title: str, body: str, state: str = "open") -> Item:
    return Item("esphome/esphome", number, "issue", state, title, body, [], "2026-09-01", None, "", "someone")


class IndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.items = [
            issue(1, "Modbus timeout after 2026.8.0 update", "### The problem\n\nmodbus frame_delay too short, sensors time out\n"
                  "### Component causing the issue\n\nmodbus\n### Anything in the logs that might be useful for us?\n\n"
                  "[W][modbus:150]: Modbus CRC Check failed! 1234!=5678"),
            issue(2, "Serial modbus broken since 2026.8", "### The problem\n\nmy modbus sensors time out now, frame delay\n"
                  "### Component causing the issue\n\nmodbus\n### Anything in the logs that might be useful for us?\n\n"
                  "[W][modbus:150]: Modbus CRC Check failed! 4321!=8765"),
            issue(3, "LVGL page flicker", "### The problem\n\nscreen flickers when switching lvgl pages\n"
                  "### Component causing the issue\n\nlvgl"),
            issue(4, "WiFi fallback hotspot only once", "### The problem\n\nfallback ap does not restart after reconnect\n"
                  "### Component causing the issue\n\nwifi"),
        ]
        self.index = Index(self.items, KNOWN)

    def test_same_bug_ranks_first_with_reasons(self) -> None:
        results = self.index.related(self.items[0], limit=3, min_score=0.0)
        score, doc, reasons = results[0]
        self.assertEqual(doc.item.number, 2)
        self.assertTrue(any("same component: modbus" in r for r in reasons))
        self.assertTrue(any("same log line" in r for r in reasons))

    def test_unrelated_is_below_threshold(self) -> None:
        numbers = [d.item.number for _, d, _ in self.index.related(self.items[2], min_score=0.25)]
        self.assertNotIn(1, numbers)
        self.assertNotIn(4, numbers)


if __name__ == "__main__":
    unittest.main()
