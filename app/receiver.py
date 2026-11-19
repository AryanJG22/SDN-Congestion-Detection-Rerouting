import socket
import struct
import time
import csv
import signal
import argparse
import json

# Packet configuration
PACKET_SIZE = 1000
HEADER_FORMAT = '!IdH'
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

# Receiver configuration
HOST = '0.0.0.0'
PORT = 5001

# Controller communication
REPORT_SOCKET = '/tmp/sdn_report.sock'

running = True


def stop_receiver(sig, frame):
    global running
    running = False


signal.signal(signal.SIGINT, stop_receiver)
signal.signal(signal.SIGTERM, stop_receiver)


def receive_packets(output_file, duration, report):

    global running

    sock = socket.socket(
        socket.AF_INET,
        socket.SOCK_DGRAM
    )

    sock.setsockopt(
        socket.SOL_SOCKET,
        socket.SO_REUSEADDR,
        1
    )

    sock.bind((HOST, PORT))
    sock.settimeout(0.2)

    report_sock = None

    if report:
        report_sock = socket.socket(
            socket.AF_UNIX,
            socket.SOCK_DGRAM
        )

    # Packet tracking
    received_sequences = set()
    missing_sequences = set()
    highest_sequence = -1

    # Per-second metrics
    interval_received = 0
    interval_bytes = 0
    delays = []
    jitter_values = []
    previous_delay = None

    start_time = time.time()
    interval_start = start_time

    print("Receiver started")
    print("Listening on port:", PORT)

    try:

        with open(output_file, 'w', newline='') as file:

            writer = csv.writer(file)

            # Exact contract column names
            writer.writerow([
                't',
                'rx_pkts',
                'lost',
                'throughput_mbps',
                'avg_delay_ms',
                'jitter_ms'
            ])

            file.flush()

            while running:

                now = time.time()

                if duration > 0 and now - start_time >= duration:
                    break

                try:
                    data, address = sock.recvfrom(2048)

                except socket.timeout:
                    data = None

                except OSError as error:
                    print("Socket error:", error)
                    break

                if data is not None:

                    recv_time = time.time()

                    # Check packet size
                    if len(data) != PACKET_SIZE:
                        print("Invalid packet size:", len(data))
                        continue

                    try:
                        seq, send_time, flow_id = struct.unpack(
                            HEADER_FORMAT,
                            data[:HEADER_SIZE]
                        )

                    except struct.error:
                        print("Invalid packet header")
                        continue

                    # Detect duplicates
                    if seq in received_sequences:
                        continue

                    received_sequences.add(seq)

                    # Detect missing and reordered packets
                    if seq > highest_sequence:

                        if highest_sequence >= 0:
                            for missing in range(
                                highest_sequence + 1, seq
                            ):
                                missing_sequences.add(missing)

                        highest_sequence = seq

                    else:
                        # A previously missing packet arrived late
                        missing_sequences.discard(seq)

                    # Calculate packet delay in milliseconds
                    delay = (recv_time - send_time) * 1000

                    delays.append(delay)

                    # Calculate jitter
                    if previous_delay is not None:
                        jitter_values.append(
                            abs(delay - previous_delay)
                        )

                    previous_delay = delay

                    interval_received += 1
                    interval_bytes += len(data)

                now = time.time()

                # Write metrics once per second
                if now - interval_start >= 1.0:

                    elapsed = now - interval_start

                    throughput = (
                        interval_bytes * 8
                    ) / (elapsed * 1_000_000)

                    avg_delay = (
                        sum(delays) / len(delays)
                        if delays else 0
                    )

                    jitter = (
                        sum(jitter_values) / len(jitter_values)
                        if jitter_values else 0
                    )

                    lost = len(missing_sequences)

                    row = [
                        round(now, 6),
                        interval_received,
                        lost,
                        round(throughput, 4),
                        round(avg_delay, 4),
                        round(jitter, 4)
                    ]

                    writer.writerow(row)
                    file.flush()

                    print("Metrics:", row)

                    # Send report to Ryu controller
                    if report_sock:

                        message = {
                            'type': 'loss_report',
                            't': now,
                            'lost': lost,
                            'rx': len(received_sequences),
                            'delay_ms': avg_delay
                        }

                        try:
                            report_sock.sendto(
                                json.dumps(message).encode(),
                                REPORT_SOCKET
                            )

                        except OSError as error:
                            print("Controller unavailable:", error)

                    # Reset interval measurements
                    interval_received = 0
                    interval_bytes = 0
                    delays.clear()
                    jitter_values.clear()

                    interval_start = now

            # Always write a final row
            now = time.time()

            elapsed = now - interval_start

            throughput = (
                interval_bytes * 8 / (elapsed * 1_000_000)
                if elapsed > 0 else 0
            )

            avg_delay = (
                sum(delays) / len(delays)
                if delays else 0
            )

            jitter = (
                sum(jitter_values) / len(jitter_values)
                if jitter_values else 0
            )

            writer.writerow([
                round(now, 6),
                interval_received,
                len(missing_sequences),
                round(throughput, 4),
                round(avg_delay, 4),
                round(jitter, 4)
            ])

            file.flush()

    finally:
        sock.close()

        if report_sock:
            report_sock.close()

    print("\nReceiver stopped")
    print("Total unique packets received:", len(received_sequences))
    print("Currently detected missing packets:", len(missing_sequences))


if __name__ == '__main__':

    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--output',
        default='results/receiver.csv'
    )

    parser.add_argument(
        '--duration',
        type=int,
        default=60
    )

    parser.add_argument(
        '--report',
        action='store_true'
    )

    args = parser.parse_args()

    receive_packets(
        args.output,
        args.duration,
        args.report
    )
