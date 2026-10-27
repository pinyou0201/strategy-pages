"""Hermetic checks for the Pages self-healing workflow (no network)."""
import sys
from pathlib import Path
import unittest
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import recover_pages


class Clock:
    def __init__(self):
        self.t = 0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class PagesRecoveryTests(unittest.TestCase):
    def test_exact_public_bytes_need_no_rebuild(self):
        count = []
        result = recover_pages.recover(
            b"verified page", "head", live=lambda _: b"verified page",
            current_head=lambda: "head", request_build=lambda: count.append(1),
            warmup=0, settle=0)
        self.assertEqual(result, "verified")
        self.assertEqual(count, [])

    def test_stale_page_triggers_rebuild_then_requires_matching_bytes(self):
        clock = Clock()
        built = []
        def live(_):
            return b"new page" if built else b"stale page"
        result = recover_pages.recover(
            b"new page", "head", live=live,
            current_head=lambda: "head", request_build=lambda: built.append(1),
            clock=clock.now, sleep=clock.sleep, warmup=15, settle=60, poll=5)
        self.assertEqual(result, "verified")
        self.assertEqual(len(built), 1)

    def test_failed_rebuild_never_marks_stale_page_green(self):
        clock = Clock()
        built = []
        with self.assertRaisesRegex(RuntimeError, "did not publish exact"):
            recover_pages.recover(
                b"new page", "head", live=lambda _: b"stale page",
                current_head=lambda: "head", request_build=lambda: built.append(1),
                clock=clock.now, sleep=clock.sleep, warmup=0, settle=15, poll=5)
        self.assertEqual(len(built), 1)

    def test_public_outage_does_not_cause_blind_redeploy(self):
        clock = Clock()
        built = []
        def offline(_):
            raise URLError("network unavailable")
        with self.assertRaisesRegex(RuntimeError, "do not force a build"):
            recover_pages.recover(
                b"new page", "head", live=offline,
                current_head=lambda: "head", request_build=lambda: built.append(1),
                clock=clock.now, sleep=clock.sleep, warmup=15, settle=15, poll=5)
        self.assertEqual(built, [])

    def test_new_master_commit_supersedes_old_publication(self):
        clock = Clock()
        heads = iter(["head", "new-head"])
        built = []
        result = recover_pages.recover(
            b"new page", "head", live=lambda _: b"old page",
            current_head=lambda: next(heads), request_build=lambda: built.append(1),
            clock=clock.now, sleep=clock.sleep, warmup=15, settle=15, poll=5)
        self.assertEqual(result, "superseded")
        self.assertEqual(built, [])


if __name__ == "__main__":
    unittest.main()
