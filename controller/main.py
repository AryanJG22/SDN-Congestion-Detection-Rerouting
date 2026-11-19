"""The ONLY Ryu app (Person 2). Run from the project root:
    MODE=adaptive RUN_DIR=results/x ~/ryuenv/bin/ryu-manager controller/main.py
"""
import json
import logging
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ryu.base import app_manager                                     # noqa: E402
from ryu.controller import ofp_event                                 # noqa: E402
from ryu.controller.handler import (MAIN_DISPATCHER, DEAD_DISPATCHER,  # noqa: E402
                                    set_ev_cls)
from ryu.lib import hub                                              # noqa: E402
from ryu.ofproto import ofproto_v1_3                                 # noqa: E402

from forwarding import Forwarding                                    # noqa: E402

try:
    from monitor import Monitor                                      # Person 3
except ImportError:                                                  # allow testing without it
    Monitor = None

SOCK = "/tmp/sdn_report.sock"


class SDNMain(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(SDNMain, self).__init__(*args, **kwargs)
        self.mode = os.environ.get("MODE", "adaptive").lower()
        paths = os.environ.get("PATHS_FILE", "config/paths.json")
        rev = os.environ.get("PATHS_REVERSE_FILE", "config/paths_reverse.json")
        self.fw = Forwarding(paths, rev)
        self.initialised = False
        self.monitor = None
        if Monitor is not None:
            self.monitor = Monitor(self.fw, mode=self.mode,
                                   run_dir=os.environ.get("RUN_DIR", "results/manual"),
                                   paths_file=paths)
            hub.spawn(self.monitor.poll_loop)
        else:
            self.logger.warning("monitor.py not found: running forwarding only")
        hub.spawn(self._listen)
        self.logger.info("SDNMain started, mode=%s", self.mode)

    # ---- switch connect / disconnect -----------------------------------
    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.fw.datapaths[dp.id] = dp
            self.logger.info("switch s%s connected", dp.id)
            if not self.initialised and self.fw.all_switches_connected():
                self.fw.install_initial()
                self.initialised = True
                self.logger.info("initial rules installed (static P1)")
        elif ev.state == DEAD_DISPATCHER:
            self.fw.datapaths.pop(dp.id, None)
            self.logger.info("switch s%s disconnected", dp.id)

    # ---- link up / down -------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def _port_status(self, ev):
        msg = ev.msg
        dp, ofp, port = msg.datapath, msg.datapath.ofproto, msg.desc
        down = bool(port.state & ofp.OFPPS_LINK_DOWN) or \
            bool(port.config & ofp.OFPPC_PORT_DOWN) or msg.reason == ofp.OFPPR_DELETE
        if down:
            hit = self.fw.port_down(dp.id, port.port_no)
            if hit and self.monitor:
                self.monitor.log_event("LINK_DOWN", from_path="s%d:p%d" % (dp.id, port.port_no))
        else:
            self.fw.port_up(dp.id, port.port_no)

    # ---- statistics -> Person 3's Monitor --------------------------------
    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats(self, ev):
        if self.monitor:
            self.monitor.handle_port_stats(ev)

    # ---- Unix datagram socket for receiver reports / manual reroute ------
    def _listen(self):
        try:
            os.unlink(SOCK)
        except FileNotFoundError:
            pass
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.bind(SOCK)
        os.chmod(SOCK, 0o666)
        self.logger.info("listening on %s", SOCK)
        while True:
            try:
                data = s.recv(65535)
                msg = json.loads(data.decode())
            except Exception as exc:
                self.logger.warning("bad datagram: %s", exc)
                hub.sleep(0.1)
                continue
            if self.monitor:
                try:
                    self.monitor.handle_report(msg)
                except Exception:
                    self.logger.exception("handle_report failed")
