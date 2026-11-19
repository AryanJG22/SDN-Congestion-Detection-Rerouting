#!/usr/bin/env python3
"""analysis/util_plots.py  (Person 3)

Single run:   python3 analysis/util_plots.py results/adaptive_8_20261120-101500
              -> <run_dir>/run_plot.png and a printed summary
Compare runs: python3 analysis/util_plots.py --compare results/static_* results/adaptive_*
              -> results/comparison/comparison.{csv,md,png}

Inputs per run (contract formats): util.csv, events.csv, receiver.csv.
Empty, header-only or missing files never crash the script.

Metric definitions
  baseline        mean receiver throughput over [t0+1, t0+bg_start) (before congestion)
  dip             first receiver sample at/after congestion onset below 90% of baseline
  recovered       first sample after the dip where throughput is >= 90% of baseline
                  for `sustain` consecutive samples
  detection_delay CONGESTION_DETECTED - (t0 + bg_start)
  reroute_time    REROUTE_DONE - CONGESTION_DETECTED   (or - LINK_DOWN on a failure run)
  thr_recovery    recovered - CONGESTION_DETECTED      (from the dip start if never detected)
t0 is the first receiver row (app start); bg_start defaults to 15 s (contract timeline).
"""
import argparse
import csv
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")                      # headless: works over SSH / in the VM
import matplotlib.pyplot as plt            # noqa: E402

THRESHOLD_PCT = 80.0
BG_START_S = 15.0
RECOVERY_FRACTION = 0.9
SUSTAIN = 2

EVENT_STYLE = {
    "CONGESTION_DETECTED": ("tab:red", "--"),
    "REROUTE_DONE": ("tab:green", "-"),
    "LINK_DOWN": ("black", ":"),
    "MANUAL_REROUTE": ("tab:purple", "-."),
}
PATH_COLORS = {"P1": "tab:blue", "P2": "tab:orange", "P3": "tab:green"}


# ----------------------------------------------------------------- loading
def _read_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def load_run(run_dir):
    util, events, rx = [], [], []
    for r in _read_rows(os.path.join(run_dir, "util.csv")):
        try:
            util.append((float(r["t"]), r["path_id"], float(r["util_pct"])))
        except (KeyError, ValueError, TypeError):
            continue
    for r in _read_rows(os.path.join(run_dir, "events.csv")):
        try:
            events.append({"t": float(r["t"]), "event": r["event"],
                           "from_path": r.get("from_path", ""), "to_path": r.get("to_path", "")})
        except (KeyError, ValueError, TypeError):
            continue
    for r in _read_rows(os.path.join(run_dir, "receiver.csv")):
        try:
            rx.append({"t": float(r["t"]), "rx": float(r["rx_pkts"]), "lost": float(r["lost"]),
                       "thr": float(r["throughput_mbps"]), "delay": float(r["avg_delay_ms"])})
        except (KeyError, ValueError, TypeError):
            continue
    rx.sort(key=lambda r: r["t"])
    events.sort(key=lambda e: e["t"])
    return {"dir": run_dir, "util": util, "events": events, "rx": rx}


def parse_run_id(run_dir):
    """run_id = <mode>_<load>_<timestamp>"""
    name = os.path.basename(os.path.normpath(run_dir))
    m = re.match(r"^(static|adaptive)_(\d+(?:\.\d+)?)_", name)
    if m:
        return name, m.group(1), float(m.group(2))
    return name, "unknown", None


def start_time(run):
    if run["rx"]:
        return run["rx"][0]["t"]
    if run["util"]:
        return min(u[0] for u in run["util"])
    if run["events"]:
        return run["events"][0]["t"]
    return 0.0


def first_event(events, name, after=None):
    for e in events:
        if e["event"] == name and (after is None or e["t"] >= after):
            return e
    return None


# ----------------------------------------------------------------- metrics
def recovery_metrics(run, bg_start_s=BG_START_S, frac=RECOVERY_FRACTION, sustain=SUSTAIN):
    rx, events = run["rx"], run["events"]
    t0 = start_time(run)
    out = {"detection_delay_s": None, "reroute_time_s": None,
           "thr_recovery_s": None, "thr_status": "n/a", "baseline_mbps": None}

    detect = first_event(events, "CONGESTION_DETECTED")
    link_down = first_event(events, "LINK_DOWN")
    onset = t0 + bg_start_s
    if link_down:
        onset = min(onset, link_down["t"])
    if detect:
        out["detection_delay_s"] = detect["t"] - (t0 + bg_start_s)

    ref_event = detect or link_down
    if ref_event:
        done = first_event(events, "REROUTE_DONE", after=ref_event["t"])
        if done:
            out["reroute_time_s"] = done["t"] - ref_event["t"]

    base_rows = [r["thr"] for r in rx if t0 + 1 <= r["t"] < t0 + bg_start_s]
    if not base_rows:
        base_rows = [r["thr"] for r in rx if r["t"] < t0 + bg_start_s]
    if not base_rows:
        return out
    baseline = sum(base_rows) / len(base_rows)
    out["baseline_mbps"] = baseline
    limit = frac * baseline

    after = [r for r in rx if r["t"] >= onset]
    dip_i = next((i for i, r in enumerate(after) if r["thr"] < limit), None)
    if dip_i is None:
        out["thr_status"] = "no_dip"
        return out
    dip_t = after[dip_i]["t"]
    rec_t = None
    for i in range(dip_i, len(after)):
        window = after[i:i + sustain]
        if window and all(r["thr"] >= limit for r in window):
            rec_t = after[i]["t"]
            break
    if rec_t is None:
        out["thr_status"] = "never"
        return out
    ref_t = ref_event["t"] if ref_event else dip_t
    out["thr_recovery_s"] = max(0.0, rec_t - ref_t)
    out["thr_status"] = "recovered"
    return out


def summarize(run_dir, bg_start_s=BG_START_S):
    run = load_run(run_dir)
    run_id, mode, load = parse_run_id(run_dir)
    rx = run["rx"]
    t0 = start_time(run)

    def wmean(rows):
        n = sum(r["rx"] for r in rows)
        return sum(r["delay"] * r["rx"] for r in rows) / n if n > 0 else None

    got = sum(r["rx"] for r in rx)
    lost = sum(r["lost"] for r in rx)
    cong = [r["thr"] for r in rx if r["t"] >= t0 + bg_start_s]
    s = {
        "run_id": run_id, "mode": mode, "load_mbps": load,
        "mean_thr_mbps": sum(r["thr"] for r in rx) / len(rx) if rx else None,
        "mean_thr_congested_mbps": sum(cong) / len(cong) if cong else None,
        "loss_pct": 100.0 * lost / (got + lost) if (got + lost) > 0 else None,
        "mean_delay_ms": wmean(rx),
        "peak_util_pct": max((u[2] for u in run["util"]), default=None),
        "n_reroutes": sum(1 for e in run["events"] if e["event"] == "REROUTE_DONE"),
    }
    s.update(recovery_metrics(run, bg_start_s))
    return s


# ---------------------------------------------------------------- plotting
def plot_run(run_dir, out_path=None, bg_start_s=BG_START_S):
    run = load_run(run_dir)
    run_id = parse_run_id(run_dir)[0]
    t0 = start_time(run)
    fig, (ax_u, ax_t) = plt.subplots(2, 1, figsize=(10, 7), sharex=True)

    paths = sorted({u[1] for u in run["util"]})
    for p in paths:
        pts = sorted((u[0] - t0, u[2]) for u in run["util"] if u[1] == p)
        ax_u.plot([a for a, _ in pts], [b for _, b in pts], label=p,
                  color=PATH_COLORS.get(p), linewidth=1.8)
    if not paths:
        ax_u.text(0.5, 0.5, "no utilization data", ha="center", va="center",
                  transform=ax_u.transAxes)
    ax_u.axhline(THRESHOLD_PCT, color="gray", linestyle="--", linewidth=1,
                 label="threshold %d%%" % THRESHOLD_PCT)
    ax_u.set_ylabel("path utilization (%)")
    ax_u.set_title("Run %s" % run_id)

    ax_t.plot([r["t"] - t0 for r in run["rx"]], [r["thr"] for r in run["rx"]],
              color="tab:blue", linewidth=1.8, label="receiver throughput")
    if not run["rx"]:
        ax_t.text(0.5, 0.5, "no receiver data", ha="center", va="center",
                  transform=ax_t.transAxes)
    ax_t.set_ylabel("throughput (Mbps)")
    ax_t.set_xlabel("time since app start (s)")

    seen = set()
    for e in run["events"]:
        color, style = EVENT_STYLE.get(e["event"], ("gray", ":"))
        label = e["event"] if e["event"] not in seen else None
        seen.add(e["event"])
        for ax in (ax_u, ax_t):
            ax.axvline(e["t"] - t0, color=color, linestyle=style, linewidth=1.4,
                       label=label if ax is ax_u else None)
    peak = max((u[2] for u in run["util"]), default=100.0)
    ax_u.set_ylim(-5, max(peak, 100.0) * 1.3)          # headroom so the legend never hides data
    ax_u.legend(loc="upper center", fontsize=8, ncol=4)
    ax_u.grid(alpha=0.3)
    ax_t.grid(alpha=0.3)
    fig.tight_layout()
    out_path = out_path or os.path.join(run_dir, "run_plot.png")
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


# -------------------------------------------------------------- comparison
def _fmt(v, nd=1):
    return "-" if v is None else ("%.*f" % (nd, v))


def _recovery_text(s):
    return _fmt(s["thr_recovery_s"]) if s["thr_status"] == "recovered" else s["thr_status"]


def latest_per_config(summaries):
    """If a (mode, load) was run several times, keep the latest run_id."""
    best = {}
    for s in summaries:
        key = (s["mode"], s["load_mbps"])
        if key not in best or s["run_id"] > best[key]["run_id"]:
            best[key] = s
    return sorted(best.values(), key=lambda s: (s["load_mbps"] or 0, s["mode"]))


COLUMNS = [
    ("load_mbps", "Background (Mbps)"), ("mode", "Mode"),
    ("mean_thr_congested_mbps", "Throughput during congestion (Mbps)"),
    ("loss_pct", "Loss (%)"), ("mean_delay_ms", "Mean delay (ms)"),
    ("reroute_time_s", "Reroute time (s)"), ("thr_recovery", "Throughput recovery (s)"),
]


def write_comparison(run_dirs, out_dir, bg_start_s=BG_START_S):
    os.makedirs(out_dir, exist_ok=True)
    rows = latest_per_config([summarize(d, bg_start_s) for d in run_dirs])
    table = []
    for s in rows:
        table.append([
            _fmt(s["load_mbps"], 0), s["mode"], _fmt(s["mean_thr_congested_mbps"], 2),
            _fmt(s["loss_pct"], 2), _fmt(s["mean_delay_ms"], 2),
            _fmt(s["reroute_time_s"], 2), _recovery_text(s),
        ])
    header = [h for _, h in COLUMNS]
    with open(os.path.join(out_dir, "comparison.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(table)
    with open(os.path.join(out_dir, "comparison.md"), "w") as f:
        f.write("| " + " | ".join(header) + " |\n")
        f.write("|" + "|".join(["---"] * len(header)) + "|\n")
        for r in table:
            f.write("| " + " | ".join(r) + " |\n")

    loads = sorted({s["load_mbps"] for s in rows if s["load_mbps"] is not None})
    metrics = [("mean_thr_congested_mbps", "Throughput during congestion (Mbps)"),
               ("loss_pct", "Packet loss (%)"), ("mean_delay_ms", "Mean delay (ms)")]
    if loads:
        fig, axes = plt.subplots(1, 3, figsize=(13, 4))
        width = 0.35
        for ax, (key, title) in zip(axes, metrics):
            for k, mode in enumerate(("static", "adaptive")):
                vals = []
                for load in loads:
                    m = next((s for s in rows if s["mode"] == mode and s["load_mbps"] == load), None)
                    vals.append(m[key] if m and m[key] is not None else 0)
                xs = [i + (k - 0.5) * width for i in range(len(loads))]
                ax.bar(xs, vals, width, label=mode,
                       color="tab:gray" if mode == "static" else "tab:blue")
            ax.set_xticks(range(len(loads)))
            ax.set_xticklabels(["%g" % l for l in loads])
            ax.set_xlabel("background load (Mbps)")
            ax.set_title(title, fontsize=10)
            ax.grid(axis="y", alpha=0.3)
        axes[0].legend()
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "comparison.png"), dpi=150)
        plt.close(fig)
    return rows


# --------------------------------------------------------------------- CLI
def print_summary(s):
    print("Run %s (%s, %s Mbps background)" % (s["run_id"], s["mode"], _fmt(s["load_mbps"], 0)))
    print("  mean throughput        : %s Mbps (congested phase: %s)" %
          (_fmt(s["mean_thr_mbps"], 2), _fmt(s["mean_thr_congested_mbps"], 2)))
    print("  loss / mean delay      : %s %% / %s ms" % (_fmt(s["loss_pct"], 2), _fmt(s["mean_delay_ms"], 2)))
    print("  peak utilization       : %s %%" % _fmt(s["peak_util_pct"]))
    print("  detection delay        : %s s" % _fmt(s["detection_delay_s"], 2))
    print("  reroute time           : %s s (%d reroute(s))" % (_fmt(s["reroute_time_s"], 2), s["n_reroutes"]))
    print("  throughput recovery    : %s s" % _recovery_text(s))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dirs", nargs="+", help="results/<run_id> directories")
    ap.add_argument("--compare", action="store_true",
                    help="build the static-vs-adaptive comparison table and chart")
    ap.add_argument("--out", help="output directory for --compare (default results/comparison)")
    ap.add_argument("--bg-start", type=float, default=BG_START_S,
                    help="background start time in s after app start (default 15)")
    args = ap.parse_args(argv)

    dirs = [d for d in args.run_dirs if os.path.isdir(d)]
    if not dirs:
        print("no run directories found", file=sys.stderr)
        return 1
    if args.compare:
        out = args.out or os.path.join("results", "comparison")
        rows = write_comparison(dirs, out, args.bg_start)
        print("wrote comparison for %d configuration(s) to %s" % (len(rows), out))
    else:
        for d in dirs:
            print_summary(summarize(d, args.bg_start))
            print("  plot: %s" % plot_run(d, bg_start_s=args.bg_start))
    return 0


if __name__ == "__main__":
    sys.exit(main())
