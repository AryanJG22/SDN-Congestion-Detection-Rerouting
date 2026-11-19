"""Forwarding layer (Person 2). Plain Python, no Ryu import: it only uses the
datapath objects handed to it, so it can be unit-tested with fakes.

Interface used by Person 3's Monitor:
    install_path(path_id), get_active_path(), get_valid_paths(), datapaths
"""
import json
import logging

LOG = logging.getLogger("forwarding")

H1, H2, H3, H4 = "10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"
PRIORITY = 100
SWITCHES = (1, 2, 3, 4, 5)
# Background flow h3<->h4 is pinned to P1: (dpid, out_port)
BG_FWD = [(1, 3), (2, 2), (5, 5)]   # h3 -> h4
BG_REV = [(5, 1), (2, 1), (1, 2)]   # h4 -> h3


class Forwarding:
    def __init__(self, paths_file="config/paths.json",
                 reverse_file="config/paths_reverse.json"):
        with open(paths_file) as f:
            self.paths = json.load(f)          # {"P1": [[dpid, out_port], ...]} toward h2
        with open(reverse_file) as f:
            self.rev = json.load(f)            # same, for h2 -> h1
        self.datapaths = {}                    # dpid -> datapath
        self.active = None
        self.down_ports = set()                # {(dpid, port_no)}
        self.initial_path = "P1"

    # ---- queries -------------------------------------------------------
    def get_active_path(self):
        return self.active

    def _path_ports(self, path_id):
        return {tuple(h) for h in self.paths[path_id]} | {tuple(h) for h in self.rev[path_id]}

    def get_valid_paths(self):
        return sorted(p for p in self.paths if not (self._path_ports(p) & self.down_ports))

    def all_switches_connected(self):
        return all(d in self.datapaths for d in SWITCHES)

    # ---- link state ----------------------------------------------------
    def port_down(self, dpid, port_no):
        self.down_ports.add((dpid, port_no))
        hit = [p for p in self.paths if (dpid, port_no) in self._path_ports(p)]
        LOG.info("port down s%s:%s affects %s", dpid, port_no, hit)
        return hit

    def port_up(self, dpid, port_no):
        self.down_ports.discard((dpid, port_no))
        hit = [p for p in self.paths if (dpid, port_no) in self._path_ports(p)]
        LOG.info("port up s%s:%s affects %s", dpid, port_no, hit)
        return hit

    # ---- installing rules ----------------------------------------------
    def install_path(self, path_id):
        """Move the h1<->h2 app flow to path_id (forward + reverse, all switches)."""
        if path_id not in self.paths:
            raise ValueError("unknown path %s" % path_id)
        if path_id not in self.get_valid_paths():
            raise ValueError("path %s has a down link" % path_id)
        fwd = {int(d): int(p) for d, p in self.paths[path_id]}
        rev = {int(d): int(p) for d, p in self.rev[path_id]}
        missing = [d for d in fwd if d not in self.datapaths]
        if missing:
            raise RuntimeError("switches not connected: %s" % missing)
        for dpid, dp in list(self.datapaths.items()):
            if dpid in fwd:
                self._add(dp, H1, H2, fwd[dpid])
                self._add(dp, H2, H1, rev[dpid])
            else:
                self._delete(dp, H1, H2)
                self._delete(dp, H2, H1)
        self.active = path_id
        LOG.info("app flow now on %s", path_id)

    def install_background(self):
        for dpid, port in BG_FWD:
            self._add(self.datapaths[dpid], H3, H4, port)
        for dpid, port in BG_REV:
            self._add(self.datapaths[dpid], H4, H3, port)

    def install_initial(self):
        self.install_background()
        self.install_path(self.initial_path)

    # ---- OpenFlow helpers ----------------------------------------------
    @staticmethod
    def _match(parser, src, dst):
        return parser.OFPMatch(eth_type=0x0800, ipv4_src=src, ipv4_dst=dst)

    def _add(self, dp, src, dst, out_port):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        actions = [parser.OFPActionOutput(out_port)]
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=PRIORITY,
                                      match=self._match(parser, src, dst),
                                      instructions=inst))

    def _delete(self, dp, src, dst):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        dp.send_msg(parser.OFPFlowMod(datapath=dp, command=ofp.OFPFC_DELETE,
                                      out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY,
                                      match=self._match(parser, src, dst)))
