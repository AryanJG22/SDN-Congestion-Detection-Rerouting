#!/usr/bin/env python3
"""Non-interactive experiment driver (Person 2). Called by run_experiment.sh as root.

Timeline: t=0 app sender starts; t=bg_start background iperf3 starts (on P1);
optional link s1-s2 down at --link-down-at; run ends at t=duration (+ small tail).
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from topo import build                       # noqa: E402
from mininet.log import setLogLevel, info   # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))


def wait_connectivity(net, tries=20):
    h1, h3 = net.get('h1', 'h3')
    for _ in range(tries):
        a = h1.cmd('ping -c1 -W1 10.0.0.2')
        b = h3.cmd('ping -c1 -W1 10.0.0.4')
        if ' 0% packet loss' in a and ' 0% packet loss' in b:
            return True
        time.sleep(1)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-dir', required=True)
    ap.add_argument('--duration', type=int, default=60)
    ap.add_argument('--load', type=float, default=8.0, help='background Mbps')
    ap.add_argument('--app-rate', type=float, default=4.0)
    ap.add_argument('--bg-start', type=int, default=15)
    ap.add_argument('--link-down-at', type=float, default=-1, help='seconds; -1 = never')
    a = ap.parse_args()

    setLogLevel('info')
    os.makedirs(a.run_dir, exist_ok=True)
    net = build()
    net.start()
    net.staticArp()
    try:
        info('*** waiting for controller rules\n')
        if not wait_connectivity(net):
            info('!!! h1-h2 / h3-h4 not reachable: is Ryu running and are the rules installed?\n')
            return 1
        h1, h2, h3, h4 = net.get('h1', 'h2', 'h3', 'h4')
        h4.cmd('iperf3 -s -p 5201 > %s/iperf_server.log 2>&1 &' % a.run_dir)
        h2.cmd('python3 %s/app/receiver.py --output %s/receiver.csv --duration %d --report '
               '> %s/receiver.log 2>&1 &' % (ROOT, a.run_dir, a.duration + 4, a.run_dir))
        time.sleep(1)

        json.dump(vars(a), open(os.path.join(a.run_dir, 'meta.json'), 'w'), indent=1)
        t0 = time.time()
        h1.cmd('python3 %s/app/sender.py --rate %s --duration %d > %s/sender.log 2>&1 &'
               % (ROOT, a.app_rate, a.duration, a.run_dir))
        info('*** t=0 app started (%.1f Mbps)\n' % a.app_rate)

        todo = [(a.bg_start, 'bg')]
        if a.link_down_at >= 0:
            todo.append((a.link_down_at, 'down'))
        todo.sort()
        for t, what in todo:
            time.sleep(max(0, t0 + t - time.time()))
            if what == 'bg':
                n = max(1, a.duration - a.bg_start - 1)
                h3.cmd('iperf3 -c 10.0.0.4 -u -b %sM -t %d -p 5201 > %s/background.log 2>&1 &'
                       % (a.load, n, a.run_dir))
                info('*** t=%d background %.1f Mbps started\n' % (t, a.load))
            else:
                net.configLinkStatus('s1', 's2', 'down')
                info('*** t=%d link s1-s2 DOWN\n' % t)

        time.sleep(max(0, t0 + a.duration + 5 - time.time()))
        h2.cmd('pkill -f app/receiver.py')
        h3.cmd('pkill iperf3')
        h4.cmd('pkill iperf3')
        time.sleep(1)
        return 0
    finally:
        net.stop()


if __name__ == '__main__':
    sys.exit(main())
