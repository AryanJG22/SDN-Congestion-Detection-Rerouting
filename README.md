# Congestion Detection and Traffic Rerouting Using SDN (UDP)

Computer Networks mini-project UE25CS243A, Project 27.

Team: Arushi NS (application) , Aryan John Gonsalves (network and forwarding) , Ashwitha Devadaga (monitoring and decision)

## 1. Overview

A custom Python UDP sender/receiver sends traffic from h1 to h2 across a Mininet topology with three alternative 10 Mbps paths (P1, P2, P3) between switches s1 and s5. A Ryu OpenFlow 1.3 controller polls port statistics every second, computes path utilization, detects congestion and reroutes the application flow to the least-loaded valid path. Background iperf3 UDP traffic congests P1. We compare static routing against adaptive (SDN) routing using throughput, packet loss, delay and recovery time.

The socket application is our own raw-socket code. iperf3 is used only to create background congestion.

## 2. Topology

Hosts: h1 10.0.0.1 (app sender), h2 10.0.0.2 (app receiver), h3 10.0.0.3 (background sender), h4 10.0.0.4 (background receiver). h1 and h3 attach to s1. h2 and h4 attach to s5.

    P1 = s1 - s2 - s5
    P2 = s1 - s3 - s5
    P3 = s1 - s4 - s5

All switch-to-switch links are 10 Mbps (TCLink, queue size 100). The ports of each path are listed in config/paths.json, which defines forward application-path ports. Reverse application-path ports are defined in config/paths_reverse.json; background-flow ports are currently defined in controller/forwarding.py.

## 3. Repository layout

    app/          sender.py, receiver.py, background.sh        (Person 1)
    topology/     topo.py                                      (Person 2)
    controller/   main.py, forwarding.py                       (Person 2)
                  monitor.py                                   (Person 3)
    config/       paths.json, paths_reverse.json
    tools/        reroute.py
    analysis/     app_metrics.py (Person 1), util_plots.py (Person 3)
    tests/        unit tests and FakeForwarding stub
    results/      one folder per run: results/<mode>_<load>_<timestamp>/
    run_experiment.sh

## 4. Setup

Tested on Ubuntu 22.04 (VirtualBox VM, 4 GB RAM). All components run on one VM, so time.time() is the same clock everywhere and delay measurements are valid.

Install the tools:

    sudo apt update
    sudo apt install -y mininet openvswitch-switch iperf3 python3-pip python3-venv tcpdump wireshark

Ubuntu 22.04 has Python 3.10, but Ryu needs Python 3.9 or older, so install 3.9 next to it:

    sudo apt install -y software-properties-common
    sudo add-apt-repository ppa:deadsnakes/ppa
    sudo apt update
    sudo apt install -y python3.9 python3.9-venv python3.9-distutils

Create the Ryu environment. These exact steps worked for us. A plain pip install ryu fails on a new setuptools and quietly installs an old Ryu 2.2, so the version is pinned:

    python3.9 -m venv ~/ryuenv
    source ~/ryuenv/bin/activate
    pip install --upgrade pip
    pip install "setuptools<58" wheel
    pip install eventlet==0.30.2
    pip install ryu==4.34 --no-build-isolation
    pip install matplotlib pandas
    ryu-manager --version        # must print ryu-manager 4.34

Day-1 check. Terminal 1:

    source ~/ryuenv/bin/activate
    ryu-manager ryu.app.simple_switch_13

Terminal 2:

    sudo mn --controller=remote,ip=127.0.0.1,port=6653 --topo=single,3 --test pingall

It must print "0% dropped". Then run sudo mn -c.

## 5. Running an experiment

Activate the environment first: source ~/ryuenv/bin/activate

    MODE=static   ./run_experiment.sh <background_mbps> [duration=60]
    MODE=adaptive ./run_experiment.sh <background_mbps> [duration=60]

Examples: MODE=static ./run_experiment.sh 8 and MODE=adaptive ./run_experiment.sh 8

Timeline of one run: t=0 the application starts at 4 Mbps, t=15 the background iperf3 traffic starts on P1, t=60 the run ends. Load levels used: 4, 6 and 8 Mbps. Everything is saved to results/<mode>_<load>_<timestamp>/.

Draw the graphs for one run, and build the static-versus-adaptive comparison:

    python3 analysis/util_plots.py results/<run_id>
    python3 analysis/util_plots.py --compare <six selected congestion-run directories>

## 6. How it works

Application (app/). The sender sends 1000-byte UDP packets to h2 port 5001. The first 14 bytes are a header (sequence number, send time, flow id). The receiver detects loss from sequence gaps, and writes throughput, delay and jitter once per second. It also sends a loss report to the controller through a Unix datagram socket.

Forwarding (controller/main.py, forwarding.py). A Ryu OpenFlow 1.3 app. Flow rules match IPv4 source and destination. install_path(path_id) removes the old rules of the application flow and installs forward and reverse rules on every switch of the chosen path. Only the h1 to h2 application flow is ever rerouted. The background flow always stays on P1.

Monitoring and decision (controller/monitor.py). The Monitor class asks every switch for port statistics once per second.

1. Rate of a port = (tx_bytes now - tx_bytes before) / elapsed time. Raw counters are never used. The first sample, counter resets and stale samples are skipped.
2. Link utilization = rate / 10 Mbps. Path utilization = the highest link utilization on the path. Only switch-to-switch ports are measured. The last port goes to the host and is shared by all paths.
3. Utilization is smoothed with a 3-sample moving average.
4. Congestion is declared when the smoothed utilization of the active path is above 80 percent for 3 consecutive samples. The event CONGESTION_DETECTED is logged.
5. In adaptive mode the Monitor picks the valid path with the lowest utilization, calls install_path, and logs REROUTE_DONE. It does nothing if no other path is at least 10 points better.
6. A 10 second cooldown follows, so the controller cannot flap between paths.
7. If the active path goes down (link failure), the Monitor moves the flow to the best valid path immediately.
8. In static mode everything is logged but install_path is never called.

All thresholds live in one dictionary, DEFAULT_CONFIG, at the top of controller/monitor.py.

| Parameter | Default | Meaning |
|---|---|---|
| link_capacity_mbps | 10 | capacity of each switch-to-switch link |
| poll_interval_s | 1 | port statistics request period |
| smoothing_window | 3 | samples in the moving average |
| threshold_pct | 80 | congestion if utilization is above this |
| confirm_samples | 3 | consecutive samples above the threshold |
| cooldown_s | 10 | no decisions for this long after a reroute |
| min_improvement_pct | 10 | an alternative must be this much less loaded |

## 7. Log formats

All files are CSV with a header row. Timestamps are time.time() epoch seconds.

    receiver.csv   t, rx_pkts, lost, throughput_mbps, avg_delay_ms, jitter_ms   (one row per second)
    util.csv       t, path_id, util_pct                                         (one row per path per second)
    events.csv     t, event, from_path, to_path, util_pct
                   events: CONGESTION_DETECTED, REROUTE_DONE, LINK_DOWN, MANUAL_REROUTE

Recovery is measured in two ways. Reroute time = REROUTE_DONE minus CONGESTION_DETECTED. Throughput recovery = time until receiver throughput is back to at least 90 percent of its level before congestion.

## 8. Results

We evaluated static and adaptive SDN routing using six congestion experiments.
The application transmitted UDP traffic at a target rate of 4 Mbps, while
background traffic on P1 was configured at 4, 6, and 8 Mbps.

The table reports receiver throughput during the congested phase, estimated
packet loss, mean packet delay, reroute time, and throughput recovery.

| Background (Mbps) | Mode | Throughput during congestion (Mbps) | Loss (%) | Mean delay (ms) | Reroute time (s) | Throughput recovery (s) |
|---|---|---|---|---|---|---|
| 4 | static | 3.74 | 0.00 | 2.03 | - | never |
| 4 | adaptive | 3.74 | 0.00 | 2.59 | 0.00 | 0.0 |
| 6 | static | 3.51 | 4.62 | 111.59 | - | 0.0 |
| 6 | adaptive | 3.68 | 1.34 | 9.74 | 0.00 | 0.0 |
| 8 | static | 3.15 | 11.76 | 106.54 | - | 24.4 |
| 8 | adaptive | 3.60 | 2.83 | 12.19 | 0.01 | 1.2 |

### Key findings

At 4 Mbps background load, both modes achieved approximately 3.74 Mbps
throughput with no detected packet loss.

At 6 Mbps background load, adaptive routing reduced estimated packet loss
from 4.62% to 1.34%, while reducing mean delay from 111.59 ms to 9.74 ms.

At 8 Mbps background load, adaptive routing:
- Increased throughput from 3.15 Mbps to 3.60 Mbps (approximately 14.3%).
- Reduced estimated packet loss from 11.76% to 2.83% (approximately 75.9%).
- Reduced mean delay from 106.54 ms to 12.19 ms (approximately 88.6%).

### Link-failure validation

A separate adaptive-routing experiment tested link failure on the active P1
path. The controller logged LINK_DOWN followed by REROUTE_DONE, switching
the application flow from P1 to P3.

The recorded interval between these events was approximately 0.614 seconds.
This measures the controller's logged reroute completion interval, not
necessarily full end-to-end traffic recovery.

### Measurement notes

The receiver identifies missing UDP packets using sequence-number gaps
and removes gaps when reordered packets arrive.

The reported loss percentage is calculated using the final outstanding
missing-sequence count divided by received packets plus that count.
It is an estimate, not an exact sender-to-receiver loss percentage:
packets missing before the first received sequence or after the highest
received sequence are not included.

The comparison uses the original six congestion experiments. Separate
link-failure experiments are excluded from the congestion comparison.

These results represent individual experimental runs and should not be
interpreted as statistically established performance guarantees.

### Graphs

- `results/comparison/comparison.png`
- `results/comparison/comparison.csv`
- `results/comparison/comparison.md`
- `results/<run_id>/run_plot.png`

### Conclusion

The experiments demonstrate that adaptive SDN routing can improve UDP
application performance when the initially selected path becomes congested.

At higher background loads, rerouting the application flow to a less-loaded
path improved receiver throughput and reduced estimated packet loss and
delay compared with static routing.

The controller also demonstrated successful path switching following a
link-failure event.

The results support the effectiveness of congestion-aware SDN routing
within the tested Mininet topology and traffic conditions.

## 9. Tests

    python3 -m unittest discover -s tests -v

The Monitor tests use a FakeForwarding stub, so they run without Mininet or Ryu.

## 10. Troubleshooting

| Problem | Fix |
|---|---|
| Ryu does not import, or ryu-manager prints oslo errors | Use the Python 3.9 venv, install setuptools<58 and eventlet==0.30.2 first, then ryu==4.34 with --no-build-isolation |
| Mininet worked yesterday and fails today | sudo mn -c, kill leftover ryu-manager and iperf3, delete /tmp/sdn_report.sock, restart Open vSwitch |
| Traffic is dropped silently | Check the ports in config/paths.json against ovs-ofctl dump-ports-desc, and check that reverse rules exist (ovs-ofctl -O OpenFlow13 dump-flows s1) |
| Receiver cannot reach the controller | Hosts are in separate network namespaces. Use the Unix socket /tmp/sdn_report.sock, not 127.0.0.1 |
| Controller keeps switching paths | Keep the smoothing, the 3-sample confirmation and the 10 s cooldown. Tune only DEFAULT_CONFIG |
| Delay numbers look wrong | Everything must run on the same VM and use time.time() |
