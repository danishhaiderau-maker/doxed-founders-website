"""Resource-cap boundaries and input-pin integrity fixtures."""

from __future__ import annotations

import unittest

from research.coverage_audit.tests.test_synthetic_formats import run_group


class LimitTests(unittest.TestCase):
    def test_limits_and_integrity(self):
        run_group(self, "limits")

    def test_input_buffer_is_charged_before_read(self):
        from research.coverage_audit.tests.test_synthetic_formats import _rows_csv, _run_built, _with_profile

        built = _with_profile(_rows_csv((0, 60, 120)), memory_bytes=2048)
        report, extra = _run_built(built)
        self.assertIn("MEMORY_CAP", report["reasons"])
        self.assertEqual(extra["reads"], 0)
        self.assertNotEqual(report["status"], "COMPLETE")

    def test_gzip_allowance_is_charged_before_decompress(self):
        from research.coverage_audit.guards import AuditStop, FixedMeter, Ledger, TINY_SYNTHETIC_PROFILE
        from research.coverage_audit import parsers
        from research.coverage_audit.tests.test_synthetic_formats import BUILDERS, _gzip, _run_built

        def member_slice(events):
            for index, event in enumerate(events):
                if event[0] != "decompress":
                    continue
                if index == 0 or events[index - 1] != ("charge", event[1]):
                    return None
                return events[index - 1 :]
            return None

        def owns(events):
            window = member_slice(events)
            if window is None:
                return False
            allowance = window[0][1]
            tail = window[2:]
            if tail == [("release", allowance)]:
                return True
            if len(tail) != 2 or tail[0][0] != "release" or tail[1][0] != "release":
                return False
            slack, owned = tail[0][1], tail[1][1]
            if slack + owned != allowance or slack < 0 or owned < 0:
                return False
            if slack == 0:
                return owned == allowance
            return slack != allowance and owned != allowance and ("release", allowance) not in tail

        self.assertFalse(owns([("decompress", 8), ("charge", 8)]))
        self.assertFalse(owns([("charge", 10), ("decompress", 10), ("release", 7), ("release", 10)]))
        self.assertFalse(owns([("charge", 10), ("decompress", 10), ("release", 7), ("release", 3), ("release", 3)]))
        self.assertTrue(owns([("charge", 10), ("decompress", 10), ("release", 10)]))
        self.assertTrue(owns([("charge", 10), ("decompress", 10), ("release", 7), ("release", 3)]))

        events = []
        real_charge = Ledger.charge_before_alloc
        real_release = Ledger.release_memory
        real_decompressobj = parsers.zlib.decompressobj

        def charge(self, nbytes):
            events.append(("charge", nbytes))
            return real_charge(self, nbytes)

        def release(self, amount):
            events.append(("release", amount))
            return real_release(self, amount)

        class _Recording:
            def __init__(self, inner):
                self._inner = inner

            def decompress(self, data, max_length=-1):
                events.append(("decompress", max_length))
                return self._inner.decompress(data, max_length)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        def decompressobj(*args, **kwargs):
            return _Recording(real_decompressobj(*args, **kwargs))

        Ledger.charge_before_alloc = charge
        Ledger.release_memory = release
        parsers.zlib.decompressobj = decompressobj
        try:
            single, _extra = _run_built(BUILDERS["gzip_single"]())
            self.assertEqual(single["status"], "COMPLETE")
            self.assertIsNotNone(member_slice(events))
            single_events = list(events)
            events.clear()
            bomb, _extra = _run_built(BUILDERS["gzip_bomb"]())
            self.assertIn("DECOMPRESSION_CAP", bomb["reasons"])
            self.assertNotIn("MEMORY_CAP", bomb["reasons"])
            self.assertTrue(owns(events))
            self.assertEqual(member_slice(events)[2:], [("release", member_slice(events)[0][1])])
            events.clear()
            flipped, _extra = _run_built(BUILDERS["gzip_flip"]())
            self.assertIn("GZIP_CRC", flipped["reasons"])
            self.assertTrue(owns(events))
            self.assertNotIn(("release", member_slice(events)[0][1]), member_slice(events)[2:])
            events.clear()
            tailed, _extra = _run_built(BUILDERS["gzip_tail"]())
            self.assertIn("GZIP_TRUNCATED_TAIL", tailed["reasons"])
            self.assertTrue(owns(events))
            self.assertNotIn(("release", member_slice(events)[0][1]), member_slice(events)[2:])
            self.assertIsNotNone(member_slice(single_events))
        finally:
            Ledger.charge_before_alloc = real_charge
            Ledger.release_memory = real_release
            parsers.zlib.decompressobj = real_decompressobj

        releases = []

        def fail_charge(self, nbytes):
            raise AuditStop("MEMORY_CAP")

        def record_release(self, amount):
            releases.append(amount)
            return real_release(self, amount)

        payload = b"event_ts,note\n1700000000000,ok\n"
        ledger = Ledger(TINY_SYNTHETIC_PROFILE, FixedMeter(0, 0))
        Ledger.charge_before_alloc = fail_charge
        Ledger.release_memory = record_release
        try:
            with self.assertRaises(AuditStop) as caught:
                parsers._one_gzip(_gzip(payload), 0, ledger, False)
            self.assertEqual(caught.exception.code, "MEMORY_CAP")
            self.assertEqual(releases, [])
        finally:
            Ledger.charge_before_alloc = real_charge
            Ledger.release_memory = real_release


if __name__ == "__main__":
    unittest.main()
