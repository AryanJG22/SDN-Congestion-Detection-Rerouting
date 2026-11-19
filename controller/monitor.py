"""controller/monitor.py  (Person 3: monitoring and decision owner)

Plain Python class used by controller/main.py (Person 2). No Ryu import at
module level, so the decision logic can be unit-tested without Ryu installed.

Public interface (per the shared contract):
    Monitor(forwarding, mode=None, run_dir=None, paths_file=..., config=None)
    poll_loop()               Ryu green thread: request port stats every 1 s, then tick()
    handle_port_stats(ev)     called by main.py from its EventOFPPortStatsReply handler
    handle_report(msg)        called by the Unix-socket listener (dict, already JSON-decoded)
    log_event(...)            append a row to events.csv (Person 2 can reuse it for LINK_DOWN)

Uses from forwarding (Person 2):
    install_path(path_id), get_active_path(), get_valid_paths(), datapaths
"""
import csv
import json
import logging
import os
import time
from collections import deque

LOG = logging.getLogger("monitor")

# Every tunable lives here (contract defaults).
DEFAULT_CONFIG = {
    "link_capacity_mbps": 10.0,   # all switch-to-switch links are 10 Mbps
    "poll_interval_s": 1.0,       # port-stats request period
    "smoothing_window": 3,        # moving average over this many samples
    "threshold_pct": 80.0,        # congestion if smoothed util is ABOVE this
    "confirm_samples": 3,         # ...for this many consecutive samples
    "cooldown_s": 10.0,           # no decisions for this long after a reroute
    "min_improvement_pct": 10.0,  # alternative must be this much lower than active
    "stale_after_s": 3.0,         # ignore link rates older than this
}

UTIL_HEADER = ["t", "path_id", "util_pct"]
EVENT_HEADER = ["t", "event", "from_path", "to_path", "util_pct"]


class Monitor:
    def __init__(self, forwarding, mode=None, run_dir=None,
                 paths_file="config/paths.json", config=None, clock=time.time):
        self.fw = forwarding
        self.mode = (mode or os.environ.get("MODE", "adaptive")).lower()
        self.cfg = dict(DEFAULT_CONFIG)
        if config:
            self.cfg.update(config)
        self.clock = clock

        self.run_dir = run_dir or os.environ.get("RUN_DIR", "results/manual")
        os.makedirs(self.run_dir, exist_ok=True)
        self.util_file = os.path.join(self.run_dir, "util.csv")
        self.events_file = os.path.join(self.run_dir, "events.csv")
        self._ensure_header(self.util_file, UTIL_HEADER)
        self._ensure_header(self.events_file, EVENT_HEADER)

        with open(paths_file) as f:
            self.paths = json.load(f)          # {"P1": [[dpid, out_port], ...], ...}

        self.link_bytes = {}    # (dpid, port) -> (tx_bytes, t)  last raw sample
        self.link_rate = {}     # (dpid, port) -> (rate_mbps, t) last computed rate
        window = int(self.cfg["smoothing_window"])
        self.history = {p: deque(maxlen=window) for p in self.paths}
        self.consec = 0
        self.cooldown_until = 0.0
        self.reports = deque(maxlen=600)   # receiver loss reports
        self.events = []                   # in-memory copy of events (tests/demo)
        self.last_util = {}                # latest raw util per path
        self.last_smoothed = {}            # latest smoothed util per path

    # ------------------------------------------------------------------ CSV
    @staticmethod
    def _ensure_header(path, header):
        # events.csv may also be written by Person 2, so never truncate.
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            with open(path, "w", newline="") as f:
                csv.writer(f).writerow(header)

    @staticmethod
    def _append(path, row):
        with open(path, "a", newline="") as f:
            csv.writer(f).writerow(row)

    def log_event(self, event, from_path="", to_path="", util_pct="", t=None):
        t = self.clock() if t is None else t
        if util_pct != "":
            util_pct = round(float(util_pct), 1)
        self._append(self.events_file, [round(t, 3), event, from_path, to_path, util_pct])
        self.events.append({"t": t, "event": event, "from_path": from_path,
                            "to_path": to_path, "util_pct": util_pct})
        LOG.info("EVENT %s %s->%s util=%s", event, from_path, to_path, util_pct)

    # ------------------------------------------------------- stats collection
    def poll_loop(self):
        """Run as a Ryu green thread: hub.spawn(monitor.poll_loop)."""
        from ryu.lib import hub
        while True:
            self._request_stats()
            hub.sleep(self.cfg["poll_interval_s"])   # replies arrive meanwhile
            try:
                self.tick()
            except Exception:
                LOG.exception("tick failed")

    def _request_stats(self):
        for dp in list(self.fw.datapaths.values()):
            try:
                ofp, parser = dp.ofproto, dp.ofproto_parser
                dp.send_msg(parser.OFPPortStatsRequest(dp, 0, ofp.OFPP_ANY))
            except Exception:
                LOG.exception("stats request failed for dp %s", getattr(dp, "id", "?"))

    def handle_port_stats(self, ev):
        msg = ev.msg
        dpid = msg.datapath.id
        now = self.clock()
        for st in msg.body:
            self.update_link(dpid, st.port_no, st.tx_bytes, now)

    def update_link(self, dpid, port, tx_bytes, now):
        """Rate = (bytes_now - bytes_prev) / dt, never the raw counter."""
        key = (dpid, port)
        prev = self.link_bytes.get(key)
        self.link_bytes[key] = (tx_bytes, now)
        if prev is None:
            return                      # first sample: no rate yet
        prev_bytes, prev_t = prev
        dt = now - prev_t
        if dt <= 0 or tx_bytes < prev_bytes:
            self.link_rate.pop(key, None)   # counter reset / bad dt: skip this sample
            return
        mbps = (tx_bytes - prev_bytes) * 8.0 / dt / 1e6
        self.link_rate[key] = (mbps, now)

    def _measured_hops(self, path_id):
        """Inter-switch egress ports only. The last hop is the switch->host port,
        which is shared by every path, so it would add a constant to all of them."""
        return self.paths[path_id][:-1]

    def _path_util(self, path_id, now):
        """Max link utilisation (%) over the path, or None if any hop has no fresh rate."""
        cap = self.cfg["link_capacity_mbps"]
        worst = 0.0
        for dpid, port in self._measured_hops(path_id):
            entry = self.link_rate.get((dpid, port))
            if entry is None or now - entry[1] > self.cfg["stale_after_s"]:
                return None
            worst = max(worst, entry[0] / cap * 100.0)
        return worst

    # --------------------------------------------------------------- decision
    def tick(self, now=None):
        now = self.clock() if now is None else now
        raw = {p: self._path_util(p, now) for p in self.paths}
        self.process_utilization(now, raw)

    def process_utilization(self, now, raw):
        """raw: {path_id: util_pct or None}. Logs util.csv, then decides."""
        smoothed = {}
        for p, u in raw.items():
            if u is None:
                continue
            self.history[p].append(u)
            smoothed[p] = sum(self.history[p]) / len(self.history[p])
            self._append(self.util_file, [round(now, 3), p, round(u, 1)])
        self.last_util = {p: u for p, u in raw.items() if u is not None}
        self.last_smoothed = smoothed

        active = self.fw.get_active_path()
        if active is None or active not in smoothed:
            return
        valid = list(self.fw.get_valid_paths())

        # Link failure: active path no longer valid -> move immediately.
        if active not in valid:
            if self.mode == "adaptive":
                best = self._best_alternative(active, valid, smoothed,
                                              need_gain=False, allow_unmeasured=True)
                if best:
                    self._reroute(active, best, smoothed[active])
            return

        # Cooldown after a reroute / detection: no decisions, no counting.
        if now < self.cooldown_until:
            self.consec = 0
            return

        if smoothed[active] > self.cfg["threshold_pct"]:
            self.consec += 1
        else:
            self.consec = 0
        if self.consec < self.cfg["confirm_samples"]:
            return

        # Congestion confirmed.
        self.consec = 0
        self.log_event("CONGESTION_DETECTED", from_path=active, util_pct=smoothed[active], t=now)
        self.cooldown_until = now + self.cfg["cooldown_s"]   # also stops log spam in static mode
        if self.mode != "adaptive":
            return                      # static: log only, never reroute
        best = self._best_alternative(active, valid, smoothed,
                                      need_gain=True, allow_unmeasured=False)
        if best:
            self._reroute(active, best, smoothed[active])

    def _best_alternative(self, active, valid, smoothed, need_gain, allow_unmeasured):
        cands = []
        for p in valid:
            if p == active:
                continue
            if p in smoothed:
                cands.append((smoothed[p], p))
            elif allow_unmeasured:
                cands.append((0.0, p))
        if not cands:
            return None
        util, best = min(cands)         # lowest utilisation, ties -> lowest name
        if need_gain and util > smoothed[active] - self.cfg["min_improvement_pct"]:
            return None                 # nothing meaningfully better; staying put
        return best

    def _reroute(self, frm, to, util):
        try:
            self.fw.install_path(to)
        except Exception:
            LOG.exception("install_path(%s) failed", to)
            return False
        self.log_event("REROUTE_DONE", from_path=frm, to_path=to, util_pct=util)
        self.cooldown_until = self.clock() + self.cfg["cooldown_s"]
        self.consec = 0
        return True

    # --------------------------------------------------------- socket reports
    def handle_report(self, msg):
        """msg: dict decoded from one datagram on /tmp/sdn_report.sock."""
        kind = msg.get("type")
        if kind == "loss_report":
            self.reports.append(msg)
        elif kind == "manual_reroute":
            self._manual_reroute(msg.get("path"))
        else:
            LOG.warning("unknown report type: %r", kind)

    def _manual_reroute(self, path_id):
        if self.mode != "adaptive":
            LOG.warning("manual_reroute ignored in %s mode", self.mode)
            return
        if path_id not in self.paths or path_id not in self.fw.get_valid_paths():
            LOG.warning("manual_reroute to invalid path %r ignored", path_id)
            return
        frm = self.fw.get_active_path()
        self.fw.install_path(path_id)
        self.log_event("MANUAL_REROUTE", from_path=frm or "", to_path=path_id)
        self.cooldown_until = self.clock() + self.cfg["cooldown_s"]
        self.consec = 0
