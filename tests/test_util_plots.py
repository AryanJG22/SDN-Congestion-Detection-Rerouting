"""Run from the repo root:  python3 -m unittest discover -s tests -v"""
import csv
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
import util_plots as up                         # noqa: E402

T0 = 1000.0


def write(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def make_run(base, name, thr, events=(), util=None, lost=None):
    """thr: list of per-second throughput values starting at T0."""
    d = os.path.join(base, name)
    os.makedirs(d)
    write(os.path.join(d, "receiver.csv"),
          ["t", "rx_pkts", "lost", "throughput_mbps", "avg_delay_ms", "jitter_ms"],
          [[T0 + i, int(v * 125), (lost or {}).get(i, 0), v, 2.0, 0.1] for i, v in enumerate(thr)])
    write(os.path.join(d, "events.csv"), ["t", "event", "from_path", "to_path", "util_pct"],
          [[T0 + t, e, "P1", "P3" if e == "REROUTE_DONE" else "", 90] for t, e in events])
    write(os.path.join(d, "util.csv"), ["t", "path_id", "util_pct"],
          [[T0 + i, p, u] for i, vals in enumerate(util or []) for p, u in vals.items()])
    return d


def dip_series(recover_at, n=60, low=1.5, high=4.0):
    return [high if (i < 15 or i >= recover_at) else low for i in range(n)]


class UtilPlotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_adaptive_run_metrics(self):
        d = make_run(self.tmp, "adaptive_8_20261120-100000", dip_series(21),
                     events=[(18.0, "CONGESTION_DETECTED"), (18.2, "REROUTE_DONE")])
        s = up.summarize(d)
        self.assertEqual((s["mode"], s["load_mbps"]), ("adaptive", 8.0))
        self.assertAlmostEqual(s["baseline_mbps"], 4.0)
        self.assertAlmostEqual(s["detection_delay_s"], 3.0)
        self.assertAlmostEqual(s["reroute_time_s"], 0.2)
        self.assertEqual(s["thr_status"], "recovered")
        self.assertAlmostEqual(s["thr_recovery_s"], 3.0)      # recovered at 21, detected at 18
        self.assertEqual(s["n_reroutes"], 1)

    def test_static_run_never_recovers(self):
        d = make_run(self.tmp, "static_8_20261120-100500", dip_series(10_000),
                     events=[(18.0, "CONGESTION_DETECTED")])
        s = up.summarize(d)
        self.assertEqual(s["thr_status"], "never")
        self.assertIsNone(s["thr_recovery_s"])
        self.assertIsNone(s["reroute_time_s"])

    def test_no_dip_when_load_is_light(self):
        d = make_run(self.tmp, "static_4_20261120-101000", [4.0] * 60)
        self.assertEqual(up.summarize(d)["thr_status"], "no_dip")

    def test_link_failure_reroute_time_uses_link_down(self):
        d = make_run(self.tmp, "adaptive_6_20261120-101500", dip_series(25),
                     events=[(20.0, "LINK_DOWN"), (21.5, "REROUTE_DONE")])
        s = up.summarize(d)
        self.assertAlmostEqual(s["reroute_time_s"], 1.5)

    def test_loss_and_delay(self):
        d = make_run(
            self.tmp,
            "static_6_20261120-102000",
            [4.0] * 10,
            lost={3: 125, 4: 125, 9: 125}
        )
        s = up.summarize(d)
        got = 10 * 500
        self.assertAlmostEqual(s["loss_pct"], 100.0 * 125 / (got + 125))
        self.assertAlmostEqual(s["mean_delay_ms"], 2.0)

    def test_empty_and_missing_files_do_not_crash(self):
        d = os.path.join(self.tmp, "adaptive_8_20261120-102500")
        os.makedirs(d)
        write(os.path.join(d, "receiver.csv"),
              ["t", "rx_pkts", "lost", "throughput_mbps", "avg_delay_ms", "jitter_ms"], [])
        s = up.summarize(d)                                   # util.csv / events.csv missing
        self.assertIsNone(s["mean_thr_mbps"])
        self.assertTrue(os.path.exists(up.plot_run(d)))

    def test_plot_and_comparison_outputs(self):
        util = [{"P1": 100 if 15 <= i < 20 else 40, "P2": 0, "P3": 5} for i in range(30)]
        a = make_run(self.tmp, "adaptive_8_20261120-103000", dip_series(21),
                     events=[(18.0, "CONGESTION_DETECTED"), (18.2, "REROUTE_DONE")], util=util)
        s = make_run(self.tmp, "static_8_20261120-103500", dip_series(10_000))
        self.assertTrue(os.path.exists(up.plot_run(a)))
        out = os.path.join(self.tmp, "cmp")
        rows = up.write_comparison([a, s], out)
        self.assertEqual([r["mode"] for r in rows], ["adaptive", "static"])
        for name in ("comparison.csv", "comparison.md", "comparison.png"):
            self.assertTrue(os.path.exists(os.path.join(out, name)), name)

    def test_latest_run_wins_per_config(self):
        a = make_run(self.tmp, "adaptive_8_20261120-090000", dip_series(40))
        b = make_run(self.tmp, "adaptive_8_20261120-110000", dip_series(21))
        rows = up.latest_per_config([up.summarize(a), up.summarize(b)])
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["run_id"].endswith("110000"))


if __name__ == "__main__":
    unittest.main()
