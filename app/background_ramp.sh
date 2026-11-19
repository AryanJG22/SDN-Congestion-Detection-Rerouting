#!/bin/bash

DST=${1:-10.0.0.4}
DURATION=${2:-10}

echo "Starting ramp UDP traffic to $DST"

for RATE in 2M 4M 6M 8M
do
    echo "Sending at $RATE for $DURATION seconds"

    iperf3 -c "$DST" -u -b "$RATE" -t "$DURATION" -p 5201

    sleep 2
done

echo "Ramp traffic completed"
