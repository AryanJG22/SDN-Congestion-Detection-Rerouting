import socket
import struct
import time
import argparse

# Packet configuration
PACKET_SIZE = 1000
HEADER_FORMAT = '!IdH'
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

# Receiver details
DESTINATION_IP = '10.0.0.2'
DESTINATION_PORT = 5001


def create_packet(seq, flow_id):

    send_time = time.time()

    # Create 14-byte header
    header = struct.pack(
        HEADER_FORMAT,
        seq,
        send_time,
        flow_id
    )

    # Add padding to make packet 1000 bytes
    padding = b'\0' * (PACKET_SIZE - HEADER_SIZE)

    packet = header + padding

    return packet


def sender(rate, duration, flow_id, destination_ip):

    sock = socket.socket(
        socket.AF_INET,
        socket.SOCK_DGRAM
    )

    destination = (destination_ip, DESTINATION_PORT)

    # Calculate time interval between packets
    packet_bits = PACKET_SIZE * 8
    interval = packet_bits / (rate * 1_000_000)

    start_time = time.time()
    seq = 0

    print("UDP Sender Started")
    print("Destination:", destination)
    print("Rate:", rate, "Mbps")

    try:

        while True:

            # Stop after specified duration
            if time.time() - start_time >= duration:
                break

            # Calculate scheduled sending time
            target_time = start_time + seq * interval

            sleep_time = target_time - time.time()

            if sleep_time > 0:
                time.sleep(sleep_time)

            packet = create_packet(seq, flow_id)

            sock.sendto(packet, destination)

            seq += 1

    except KeyboardInterrupt:
        print("\nSender stopped by user")

    except OSError as error:
        print("Socket error:", error)

    finally:
        sock.close()

    print("Total packets sent:", seq)


if __name__ == "__main__":

    parser = argparse.ArgumentParser()

    parser.add_argument('--rate', type=float, default=4)
    parser.add_argument('--duration', type=int, default=60)
    parser.add_argument('--flow-id', type=int, default=1)
    parser.add_argument('--dst', default=DESTINATION_IP)

    args = parser.parse_args()

    if args.rate <= 0 or args.duration <= 0:
        parser.error("Rate and duration must be positive")

    sender(
        args.rate,
        args.duration,
        args.flow_id,
        args.dst
    )
