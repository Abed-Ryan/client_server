import socket
import struct
import threading
import time
import sys

# ANSI color helpers
COLOR_RESET = "\033[0m"
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_RED = "\033[91m"
COLOR_MAGENTA = "\033[95m"
COLOR_CYAN = "\033[96m"


def cprint(text, color=COLOR_RESET, end="\n"):
    print(color + str(text) + COLOR_RESET, end=end)


MAGIC_COOKIE = 0xabcddcba
MSG_TYPE_OFFER = 0x2
MSG_TYPE_REQUEST = 0x3
MSG_TYPE_PAYLOAD = 0x4

CLIENT_LISTEN_PORT = 13117  # Where we listen for broadcast offers


# Worker thread for TCP transfer
def tcp_download_worker(server_ip, server_tcp_port, file_size, idx, results_list):
    import time
    start_time = time.time()
    total_received = 0

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((server_ip, server_tcp_port))
            # Send file_size as string + newline
            request_str = str(file_size) + "\n"
            s.sendall(request_str.encode())

            # Receive the bytes
            while total_received < file_size:
                data = s.recv(4096)
                if not data:
                    break
                total_received += len(data)
    except Exception as e:
        cprint(f"[TCP-{idx + 1}] Error: {e}", COLOR_RED)

    end_time = time.time()
    duration = end_time - start_time
    speed_bps = (total_received * 8) / duration if duration > 0 else 0
    results_list[idx] = (duration, speed_bps, total_received)


# Worker thread for UDP transfer
def udp_download_worker(server_ip, server_udp_port, file_size, idx, results_list):
    import time
    start_time = time.time()
    total_bytes_received = 0
    total_segments = None
    received_segments = set()

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(1.0)

    # Build request packet
    # Format: magic cookie (4 bytes), msg_type (1 byte), file_size (8 bytes)
    request_packet = struct.pack("!IBQ", MAGIC_COOKIE, MSG_TYPE_REQUEST, file_size)
    try:
        s.sendto(request_packet, (server_ip, server_udp_port))
    except Exception as e:
        cprint(f"[UDP-{idx + 1}] Error sending request: {e}", COLOR_RED)
        s.close()
        results_list[idx] = (0, 0, 0, 0)
        return

    # Receive payload packets until timeout
    while True:
        try:
            data, addr = s.recvfrom(65535)
            if len(data) < 21:
                continue
            cookie, msg_type = struct.unpack_from("!IB", data, 0)
            if cookie != MAGIC_COOKIE or msg_type != MSG_TYPE_PAYLOAD:
                continue

            # read total_segments and current_segment
            total_seg, current_seg = struct.unpack_from("!QQ", data, 5)
            payload = data[5 + 16:]
            total_bytes_received += len(payload)

            if total_segments is None:
                total_segments = total_seg
            received_segments.add(current_seg)

        except socket.timeout:
            # no data for 1 second => transfer done
            break
        except Exception as e:
            cprint(f"[UDP-{idx + 1}] Error: {e}", COLOR_RED)
            break

    s.close()

    end_time = time.time()
    duration = end_time - start_time
    speed_bps = (total_bytes_received * 8) / duration if duration > 0 else 0
    if total_segments is None:
        total_segments = 0
    received_count = len(received_segments)
    success_percentage = (100.0 * received_count / total_segments) if total_segments > 0 else 0.0

    results_list[idx] = (duration, speed_bps, total_bytes_received, success_percentage)


def main():
    while True:
        # Step 4: Client started, listening for offer requests
        cprint("Client started, listening for offer requests...", COLOR_YELLOW)

        # 1) Wait for an offer
        server_ip = None
        server_udp_port = None
        server_tcp_port = None

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", CLIENT_LISTEN_PORT))
            s.settimeout(5.0)

            while True:
                try:
                    data, addr = s.recvfrom(1024)
                except socket.timeout:
                    cprint("No offer received, retrying...", COLOR_RED)
                    continue

                if len(data) < 9:
                    continue
                cookie, msg_type = struct.unpack_from("!IB", data, 0)
                if cookie == MAGIC_COOKIE and msg_type == MSG_TYPE_OFFER:
                    # parse server ports
                    server_udp_port, server_tcp_port = struct.unpack_from("!HH", data, 5)
                    server_ip = addr[0]
                    cprint(f"Received offer from {server_ip}", COLOR_GREEN)
                    break  # exit the while loop

        if not server_ip:
            # If we never got an offer in time
            cprint("Could not find any server. Going back to wait...\n", COLOR_RED)
            time.sleep(2)
            continue

        # 2) Now that we have an offer, ask the user for file size, # TCP, # UDP
        try:
            file_size = int(input("Enter the file size to download (bytes): ").strip())
            num_tcp = int(input("Enter the number of TCP connections: ").strip())
            num_udp = int(input("Enter the number of UDP connections: ").strip())
        except ValueError:
            cprint("Invalid numeric input. Skipping test and listening again...", COLOR_RED)
            time.sleep(2)
            continue

        # 3) Start the speed tests
        tcp_results = [None] * num_tcp
        tcp_threads = []
        for i in range(num_tcp):
            t = threading.Thread(target=tcp_download_worker,
                                 args=(server_ip, server_tcp_port, file_size, i, tcp_results))
            t.start()
            tcp_threads.append(t)

        udp_results = [None] * num_udp
        udp_threads = []
        for i in range(num_udp):
            t = threading.Thread(target=udp_download_worker,
                                 args=(server_ip, server_udp_port, file_size, i, udp_results))
            t.start()
            udp_threads.append(t)

        # Wait for all threads to finish
        for t in tcp_threads:
            t.join()
        for t in udp_threads:
            t.join()

        # 4) Print results
        for i, (duration, speed_bps, total_recv) in enumerate(tcp_results):
            cprint(f"TCP transfer #{i + 1} finished, total time: {duration:.2f} seconds, "
                   f"total speed: {speed_bps:.2f} bits/second, bytes received: {total_recv}",
                   COLOR_GREEN)

        for i, (duration, speed_bps, total_recv, success_pct) in enumerate(udp_results):
            cprint(f"UDP transfer #{i + 1} finished, total time: {duration:.2f} seconds, "
                   f"total speed: {speed_bps:.2f} bits/second, bytes received: {total_recv}, "
                   f"percentage of packets received successfully: {success_pct:.2f}%",
                   COLOR_GREEN)

        # 5) Print done and go back to wait for another offer
        cprint("All transfers complete, listening to offer requests", COLOR_MAGENTA)
        # The loop repeats here (step 1) => wait for a new offer, ask for input, run again


if __name__ == "__main__":
    main()