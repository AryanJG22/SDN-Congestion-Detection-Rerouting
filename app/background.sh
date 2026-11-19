
#!/bin/bash

# Usage: ./app/background.sh <destination_ip> <bandwidth> <duration>

DST=${1:-10.0.0.4}
BANDWIDTH=${2:-4M}
DURATION=${3:-30}

echo "Starting UDP background traffic..."
echo "Destination: $DST"
echo "Bandwidth: $BANDWIDTH"
echo "Duration: $DURATION seconds"

iperf3 -c "$DST" -u -b "$BANDWIDTH" -t "$DURATION" -p 5201
