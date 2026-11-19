#!/usr/bin/env python3
"""Force a reroute for testing:  python3 tools/reroute.py P3
(sends a manual_reroute datagram to the controller socket)."""
import json
import socket
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "P3"
s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
s.sendto(json.dumps({"type": "manual_reroute", "path": path}).encode(), "/tmp/sdn_report.sock")
print("sent manual_reroute ->", path)
