#!/usr/bin/env python3
"""Mininet topology for Project 27 (Person 2).

h1 10.0.0.1 app sender      h2 10.0.0.2 app receiver
h3 10.0.0.3 background src  h4 10.0.0.4 background sink
h1,h3 on s1; h2,h4 on s5. Three 10 Mbps paths: s1-s2-s5, s1-s3-s5, s1-s4-s5.

Switch ports (must match config/paths.json):
  s1: 1=h1 2=h3 3=s2 4=s3 5=s4
  s2,s3,s4: 1=s1 2=s5
  s5: 1=s2 2=s3 3=s4 4=h2 5=h4
Run interactively:  sudo python3 topology/topo.py   (needs Ryu running on 6653)
"""
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel


def build():
    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink, autoSetMacs=True)
    net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)

    s = {i: net.addSwitch('s%d' % i, dpid='%016x' % i, protocols='OpenFlow13')
         for i in range(1, 6)}

    h1 = net.addHost('h1', ip='10.0.0.1/24')
    h2 = net.addHost('h2', ip='10.0.0.2/24')
    h3 = net.addHost('h3', ip='10.0.0.3/24')
    h4 = net.addHost('h4', ip='10.0.0.4/24')

    net.addLink(h1, s[1], port2=1)
    net.addLink(h3, s[1], port2=2)
    net.addLink(h2, s[5], port2=4)
    net.addLink(h4, s[5], port2=5)

    bw = dict(bw=10, max_queue_size=100)
    for mid, p1, p5 in ((2, 3, 1), (3, 4, 2), (4, 5, 3)):
        net.addLink(s[1], s[mid], port1=p1, port2=1, **bw)
        net.addLink(s[mid], s[5], port1=2, port2=p5, **bw)
    return net


if __name__ == '__main__':
    setLogLevel('info')
    net = build()
    net.start()
    net.staticArp()
    CLI(net)
    net.stop()
