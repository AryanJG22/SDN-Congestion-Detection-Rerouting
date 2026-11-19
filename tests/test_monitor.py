"""Run from the repo root:  python3 -m unittest discover -s tests -v"""
import csv
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from controller.monitor import Monitor          # noqa: E402
from fake_forwarding import FakeForwarding      # noqa: E402

# [dpid, out_port] hops toward h2; last hop is the s5 -> h2 port.
PATHS = {
    "P1": [[1, 2], [2, 2], [5, 1]],
    "P2": [[1, 3], [3, 2], [5, 1]],
    "P3": [[1, 4], [4, 2], [5, 1]],
}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def read_csv(path):
    with open(path) as f:
        return list(csv.DictReader(f))


class MonitorTests(unittest.TestCase):
    def make(self, mode="adaptive", active="P1", valid=("P1", "P2", "P3")):
        tmp = tempfile.mkdtemp()
        pf = os.path.join(tmp, "paths.json")
        with open(pf, "w") as f:
            json.dump(PATHS, f)
        self.fw = FakeForwarding(active, valid)
        self.clock = Clock()
        self.mon = Monitor(self.fw, mode=mode, run_dir=tmp, paths_file=pf, clock=self.clock)
        self.dir = tmp
        return self.mon

    def feed(self, *samples):
        for s in samples:
            self.clock.t += 1
            self.mon.process_utilization(self.clock.t, s)

    def names(self):
        return [e["event"] for e in self.mon.events]

    # ---- rate maths -------------------------------------------------------
    def set_rate(self, dpid, port, mbps, t0=100.0):
        self.mon.update_link(dpid, port, 0, t0)
        self.mon.update_link(dpid, port, int(mbps * 1e6 / 8), t0 + 1)

    def test_rate_from_byte_difference(self):
        m = self.make()
        m.update_link(1, 2, 0, 100.0)
        self.assertNotIn((1, 2), m.link_rate)                 # first sample: no rate
        m.update_link(1, 2, 1_250_000, 101.0)                 # 10 Mbit in 1 s
        self.assertAlmostEqual(m.link_rate[(1, 2)][0], 10.0)

    def test_counter_reset_is_skipped(self):
        m = self.make()
        m.update_link(1, 2, 5000, 100.0)
        m.update_link(1, 2, 100, 101.0)
        self.assertNotIn((1, 2), m.link_rate)

    def test_path_util_is_max_and_ignores_host_port(self):
        m = self.make()
        self.set_rate(1, 2, 5.0)
        self.set_rate(2, 2, 8.0)
        self.set_rate(5, 1, 10.0)                             # host-facing, must be ignored
        self.assertAlmostEqual(m._path_util("P1", 101.0), 80.0)

    def test_path_util_none_until_all_hops_have_rates(self):
        m = self.make()
        self.set_rate(1, 2, 5.0)
        self.assertIsNone(m._path_util("P1", 101.0))

    def test_stale_rate_ignored(self):
        m = self.make()
        self.set_rate(1, 2, 5.0)
        self.set_rate(2, 2, 5.0)
        self.assertIsNone(m._path_util("P1", 120.0))

    # ---- decision logic ---------------------------------------------------
    def test_three_high_samples_trigger_reroute_to_least_loaded(self):
        self.make()
        hot = {"P1": 100.0, "P2": 40.0, "P3": 10.0}
        self.feed(hot, hot, hot)
        self.assertEqual(self.fw.calls, ["P3"])
        self.assertEqual(self.names(), ["CONGESTION_DETECTED", "REROUTE_DONE"])
        done = self.mon.events[-1]
        self.assertEqual((done["from_path"], done["to_path"]), ("P1", "P3"))

    def test_two_high_samples_do_not_trigger(self):
        self.make()
        # smoothed: 100, 100, 76.7 -> counter resets before reaching 3
        self.feed({"P1": 100.0, "P2": 0.0, "P3": 0.0},
                  {"P1": 100.0, "P2": 0.0, "P3": 0.0},
                  {"P1": 30.0, "P2": 0.0, "P3": 0.0})
        self.assertEqual(self.fw.calls, [])
        self.assertEqual(self.names(), [])

    def test_exactly_threshold_is_not_congestion(self):
        self.make()
        s = {"P1": 80.0, "P2": 0.0, "P3": 0.0}
        self.feed(s, s, s, s, s)
        self.assertEqual(self.fw.calls, [])

    def test_cooldown_blocks_second_reroute_then_allows_it(self):
        self.make()
        p1 = {"P1": 100.0, "P2": 0.0, "P3": 30.0}
        self.feed(p1, p1, p1)                                  # reroute at t=3 -> P2
        self.assertEqual(self.fw.calls, ["P2"])
        p2 = {"P1": 100.0, "P2": 100.0, "P3": 30.0}
        self.feed(*[p2] * 10)                                  # t=4..13, inside cooldown
        self.assertEqual(self.fw.calls, ["P2"])
        self.feed(p2, p2, p2)                                  # t=14..16, confirmed again
        self.assertEqual(self.fw.calls, ["P2", "P3"])

    def test_no_reroute_when_alternatives_not_better(self):
        self.make()
        s = {"P1": 100.0, "P2": 95.0, "P3": 95.0}
        self.feed(s, s, s)
        self.assertEqual(self.fw.calls, [])
        self.assertEqual(self.names(), ["CONGESTION_DETECTED"])

    def test_static_mode_logs_but_never_reroutes(self):
        self.make(mode="static")
        hot = {"P1": 100.0, "P2": 0.0, "P3": 0.0}
        self.feed(hot, hot, hot)
        self.assertEqual(self.fw.calls, [])
        self.assertEqual(self.names(), ["CONGESTION_DETECTED"])

    def test_invalid_paths_are_never_chosen(self):
        self.make(valid=("P1", "P3"))
        hot = {"P1": 100.0, "P2": 0.0, "P3": 50.0}            # P2 is emptiest but down
        self.feed(hot, hot, hot)
        self.assertEqual(self.fw.calls, ["P3"])

    def test_link_failure_reroutes_immediately(self):
        self.make(valid=("P2", "P3"))                          # P1 went down
        self.feed({"P1": 10.0, "P2": 50.0, "P3": 20.0})
        self.assertEqual(self.fw.calls, ["P3"])
        self.assertEqual(self.names(), ["REROUTE_DONE"])

    # ---- socket messages --------------------------------------------------
    def test_manual_reroute(self):
        m = self.make()
        m.handle_report({"type": "manual_reroute", "path": "P3"})
        self.assertEqual(self.fw.calls, ["P3"])
        self.assertEqual(self.names(), ["MANUAL_REROUTE"])

    def test_manual_reroute_to_invalid_path_ignored(self):
        m = self.make(valid=("P1", "P2"))
        m.handle_report({"type": "manual_reroute", "path": "P3"})
        m.handle_report({"type": "manual_reroute", "path": "P9"})
        self.assertEqual(self.fw.calls, [])

    def test_loss_report_stored(self):
        m = self.make()
        m.handle_report({"type": "loss_report", "t": 1.0, "lost": 2, "rx": 500, "delay_ms": 3.1})
        self.assertEqual(len(m.reports), 1)

    # ---- CSV output -------------------------------------------------------
    def test_csv_files(self):
        self.make()
        hot = {"P1": 100.0, "P2": 40.0, "P3": 10.0}
        self.feed(hot, hot, hot)
        util = read_csv(os.path.join(self.dir, "util.csv"))
        self.assertEqual(list(util[0].keys()), ["t", "path_id", "util_pct"])
        self.assertEqual(len(util), 9)                          # 3 paths x 3 ticks
        ev = read_csv(os.path.join(self.dir, "events.csv"))
        self.assertEqual(list(ev[0].keys()), ["t", "event", "from_path", "to_path", "util_pct"])
        self.assertEqual([e["event"] for e in ev], ["CONGESTION_DETECTED", "REROUTE_DONE"])


if __name__ == "__main__":
    unittest.main()
