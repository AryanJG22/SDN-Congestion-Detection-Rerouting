
import os
import argparse
import pandas as pd
import matplotlib.pyplot as plt

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default="results/receiver.csv"
    )
    parser.add_argument(
        "--output",
        default="results/graphs"
    )
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print("Error: receiver.csv not found!")
        return

    os.makedirs(args.output, exist_ok=True)

    df = pd.read_csv(args.input)

    required = [
        "t", "rx_pkts", "lost",
        "throughput_mbps", "avg_delay_ms", "jitter_ms"
    ]

    if not all(col in df.columns for col in required):
        print("Error: CSV columns do not match!")
        return

    df = df.dropna(subset=required)

    if df.empty:
        print("No data available to plot.")
        return

    # Convert timestamps into elapsed seconds
    df["elapsed"] = df["t"] - df["t"].iloc[0]

    graphs = [
        ("throughput_mbps", "Throughput (Mbps)",
         "Throughput vs Time", "throughput.png"),
        ("lost", "Cumulative Lost Packets",
         "Packet Loss vs Time", "packet_loss.png"),
        ("avg_delay_ms", "Average Delay (ms)",
         "Average Delay vs Time", "delay.png")
    ]

    for column, ylabel, title, filename in graphs:
        plt.figure(figsize=(9, 5))
        plt.plot(df["elapsed"], df[column], marker="o")
        plt.xlabel("Time (seconds)")
        plt.ylabel(ylabel)
        plt.title(title)
        plt.grid(True)
        plt.tight_layout()
        plt.savefig(os.path.join(args.output, filename))
        plt.close()

    total_rx = df["rx_pkts"].sum()
    total_lost = df["lost"].iloc[-1] if not df.empty else 0
    loss_percent = (
        total_lost / (total_rx + total_lost) * 100
        if total_rx + total_lost > 0 else 0
    )

    print("\n========== APPLICATION SUMMARY ==========")
    print(f"Mean throughput : {df['throughput_mbps'].mean():.3f} Mbps")
    print(f"Total received  : {total_rx}")
    print(f"Total lost      : {total_lost}")
    print(f"Packet loss     : {loss_percent:.2f}%")
    print(f"Mean delay      : {df['avg_delay_ms'].mean():.3f} ms")
    print(f"Mean jitter     : {df['jitter_ms'].mean():.3f} ms")
    print("=========================================")
    print(f"Graphs saved in: {args.output}")

if __name__ == "__main__":
    main()

