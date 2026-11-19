# Demo script (about 5 minutes)

## Before the demo

    source ~/ryuenv/bin/activate
    cd ~/sdn_project
    sudo mn -c
    rm -f /tmp/sdn_report.sock
    sudo systemctl restart openvswitch-switch

Have two terminals open in the project folder. Run the whole demo once beforehand.

## Step 1: explain the idea (30 s)

Show the three paths P1, P2, P3. The app sends 4 Mbps from h1 to h2. At t=15 background traffic fills P1. The controller must notice and move the app to a free path.

## Step 2: static routing, no SDN reaction (1 min)

    MODE=static ./run_experiment.sh 8

Point out: the app stays on P1, throughput drops, packets are lost, and it never recovers.

## Step 3: adaptive routing (1 min)

    MODE=adaptive ./run_experiment.sh 8

Point out in the controller output: CONGESTION_DETECTED, then REROUTE_DONE, then throughput recovers.

## Step 4: show the graphs (1 min)

    python3 analysis/util_plots.py results/<adaptive run folder>
    python3 analysis/util_plots.py --compare results/static_* results/adaptive_*

Top graph: path utilization with the 80 percent line and the congestion and reroute markers. Bottom graph: receiver throughput dipping and recovering. Then show results/comparison/comparison.png and comparison.md.

## Step 5: optional extras (1 min)

Manual reroute: python3 tools/reroute.py P3
Link failure test: the runner can bring link s1-s2 down at a set time. The Monitor reroutes immediately.
Verify rules: sudo ovs-ofctl -O OpenFlow13 dump-flows s1

## If something goes wrong

- Nothing connects: sudo mn -c, kill ryu-manager, delete /tmp/sdn_report.sock, run again.
- Ryu not found: source ~/ryuenv/bin/activate
- No graph: check that results/<run_id>/ has util.csv, events.csv and receiver.csv.
- Keep screenshots and one finished results folder as a backup.

## Questions about my part (monitoring and decision)

- Why rate and not the raw byte counter? The counter only grows. Rate is bytes since the last sample divided by the elapsed time, and utilization is rate divided by 10 Mbps.
- Why a 3-sample moving average? A single spike should not count.
- Why 3 consecutive samples above 80 percent? To avoid reacting to short bursts.
- Why a 10 second cooldown? Without it the controller can flip between paths forever (flapping).
- Why measure only switch-to-switch ports? The last port goes to the host and is shared by every path.
- What does static mode do? It logs everything but never calls install_path. It is the baseline for comparison.
- What if a link fails? The active path becomes invalid, so the Monitor moves to the best valid path at once.
- How are the Monitor and the forwarding layer connected? Only through install_path, get_active_path, get_valid_paths and datapaths, as agreed in the contract.
