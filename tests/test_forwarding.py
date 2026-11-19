import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "controller"))
from forwarding import Forwarding  # noqa: E402


class Parser:
    def OFPMatch(self, **kw): return ("match", tuple(sorted(kw.items())))
    def OFPActionOutput(self, port): return ("out", port)
    def OFPInstructionActions(self, t, a): return ("inst", a)
    def OFPFlowMod(self, **kw): return kw


class Ofp:
    OFPIT_APPLY_ACTIONS = 4
    OFPFC_DELETE = 3
    OFPP_ANY = 0xffffffff
    OFPG_ANY = 0xffffffff


class FakeDp:
    ofproto = Ofp()
    ofproto_parser = Parser()

    def __init__(self, i): self.id = i; self.sent = []
    def send_msg(self, m): self.sent.append(m)


def make():
    fw = Forwarding(os.path.join(ROOT, "config/paths.json"), os.path.join(ROOT, "config/paths_reverse.json"))
    for i in range(1, 6):
        fw.datapaths[i] = FakeDp(i)
    return fw


def adds(dp): return [m for m in dp.sent if "instructions" in m]
def dels(dp): return [m for m in dp.sent if m.get("command") == 3]


class T(unittest.TestCase):
    def test_initial(self):
        fw = make(); fw.install_initial()
        self.assertEqual(fw.get_active_path(), "P1")
        self.assertEqual(len(adds(fw.datapaths[1])), 4)   # bg fwd/rev + app fwd/rev on s1
        self.assertEqual(len(dels(fw.datapaths[3])), 2)   # s3 not on P1

    def test_reroute_p3(self):
        fw = make(); fw.install_initial()
        for d in fw.datapaths.values(): d.sent.clear()
        fw.install_path("P3")
        self.assertEqual(fw.get_active_path(), "P3")
        self.assertEqual(len(adds(fw.datapaths[4])), 2)
        self.assertEqual(len(dels(fw.datapaths[2])), 2)   # old P1 mid switch cleaned
        s1_out = sorted(m["instructions"][0][1][0][1] for m in adds(fw.datapaths[1]))
        self.assertEqual(s1_out, [1, 5])                   # fwd via port 5 (s4), rev to h1 port 1

    def test_link_down(self):
        fw = make()
        fw.port_down(1, 3)
        self.assertEqual(fw.get_valid_paths(), ["P2", "P3"])
        with self.assertRaises(ValueError):
            fw.install_path("P1")
        fw.port_up(1, 3)
        self.assertEqual(fw.get_valid_paths(), ["P1", "P2", "P3"])

    def test_missing_switch(self):
        fw = make(); del fw.datapaths[4]
        with self.assertRaises(RuntimeError):
            fw.install_path("P3")


if __name__ == "__main__":
    unittest.main()
