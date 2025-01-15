#!/usr/bin/env python3
import socket
import struct
import threading
import time
import sys

# ANSI colors for terminal fun
COLOR_RESET = "\033[0m"
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_RED = "\033[91m"
COLOR_MAGENTA = "\033[95m"
COLOR_CYAN = "\033[96m"


def cprint(text, color=COLOR_RESET, end="\n"):
    print(color + str(text) + COLOR_RESET, end=end)


# Protocol constants
MAGIC_COOKIE = 0xabcddcba
MSG_TYPE_OFFER = 0x2
MSG_TYPE_REQUEST = 0x3
MSG_TYPE_PAYLOAD = 0x4

# Broadcast settings
BROADCAST_INTERVAL = 1.0  # in seconds
BROADCAST_LISTEN_PORT = 13117  # Clients listen here

# For demonstration, we default these to some ports
DEFAULT_UDP_PORT = 2025
DEFAULT_TCP_PORT = 2026
CHUNK_SIZE = 1024


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except:
        return "127.0.0.1"
    finally:
        s.close()

def handle_tcp_connection(conn_socket, client_address):
    """
    Handle a single TCP connection from the client.
    - Reads the file size from the client (ASCII + newline).
    - Sends that many bytes in CHUNK_SIZE blocks.
    """
    try:
        data = b""
        while True:
            chunk = conn_socket.recv(1024)
            if not chunk:
                # Client might have closed the connection
                cprint(f"[TCP] {client_address} disconnected unexpectedly.", COLOR_RED)
                return
            data += chunk
            if b"\n" in data:
                # We read until newline
                break

        line = data.decode().strip()
        try:
            file_size = int(line)
        except ValueError:
            cprint(f"[TCP] Invalid file size '{line}' from {client_address}. Closing.", COLOR_RED)
            return

        cprint(f"[TCP] {client_address} requested {file_size} bytes.", COLOR_CYAN)

        # Send data
        bytes_sent = 0
        dummy_chunk = b"A" * CHUNK_SIZE
        while bytes_sent < file_size:
            remaining = file_size - bytes_sent
            to_send = min(remaining, CHUNK_SIZE)
            conn_socket.sendall(dummy_chunk[:to_send])
            bytes_sent += to_send

        cprint(f"[TCP] Done sending {file_size} bytes to {client_address}", COLOR_GREEN)

    except Exception as e:
        cprint(f"[TCP] Error with {client_address}: {e}", COLOR_RED)
    finally:
        conn_socket.close()


def handle_udp_request(server_udp_socket, client_address, file_size):
    """
    Handle a single UDP request from the client.
    - Sends 'file_size' bytes in multiple packets.
    - Each packet has a header with magic cookie, msg type, total segments, current segment index.
    """
    total_segments = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE  # round up
    cprint(f"[UDP] {client_address} requested {file_size} bytes => {total_segments} segments.", COLOR_CYAN)

    dummy_data = b"B" * CHUNK_SIZE
    for seg_idx in range(total_segments):
        current_offset = seg_idx * CHUNK_SIZE
        remaining = file_size - current_offset
        payload_size = min(CHUNK_SIZE, remaining)

        # Build the header
        header = struct.pack("!IBQQ",
                             MAGIC_COOKIE,  # 4 bytes
                             MSG_TYPE_PAYLOAD,  # 1 byte
                             total_segments,  # 8 bytes
                             seg_idx)  # 8 bytes
        packet = header + dummy_data[:payload_size]

        try:
            server_udp_socket.sendto(packet, client_address)
        except Exception as e:
            cprint(f"[UDP] Error sending seg {seg_idx} to {client_address}: {e}", COLOR_RED)
            break

    cprint(f"[UDP] Finished sending {file_size} bytes to {client_address}", COLOR_GREEN)


def broadcast_offers(stop_event, udp_port, tcp_port):
    """
    Constantly broadcast "offer" messages (UDP) to 255.255.255.255:13117
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.2)

    while not stop_event.is_set():
        # Offer packet: magic cookie (4 bytes), msg type (1 byte), serverUDP (2 bytes), serverTCP (2 bytes)
        offer = struct.pack("!IBHH", MAGIC_COOKIE, MSG_TYPE_OFFER, udp_port, tcp_port)
        try:
            sock.sendto(offer, ("255.255.255.255", BROADCAST_LISTEN_PORT))
        except Exception as e:
            cprint(f"[OFFER] Broadcast error: {e}", COLOR_RED)

        time.sleep(BROADCAST_INTERVAL)
    sock.close()


def main():
    # Optionally parse command-line arguments for custom ports
    udp_port = DEFAULT_UDP_PORT
    tcp_port = DEFAULT_TCP_PORT
    if len(sys.argv) >= 3:
        udp_port = int(sys.argv[1])
        tcp_port = int(sys.argv[2])

    # Start broadcast thread
    stop_event = threading.Event()
    bcast_thread = threading.Thread(target=broadcast_offers, args=(stop_event, udp_port, tcp_port), daemon=True)
    bcast_thread.start()

    # Setup TCP listening
    tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp_sock.bind(("0.0.0.0", tcp_port))
    tcp_sock.listen(5)

    # Setup UDP listening
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    udp_sock.bind(("0.0.0.0", udp_port))
    udp_sock.settimeout(1.0)

    # Print server info
    local_ip = get_local_ip()
    cprint(f"Server started, listening on IP address {local_ip}", COLOR_MAGENTA)

    try:
        while True:
            # Handle UDP requests
            try:
                data, addr = udp_sock.recvfrom(1024)
                if len(data) < 13:
                    continue
                cookie, msg_type = struct.unpack_from("!IB", data, 0)
                if cookie != MAGIC_COOKIE or msg_type != MSG_TYPE_REQUEST:
                    continue
                # parse file size
                file_size = struct.unpack_from("!Q", data, 5)[0]
                cprint(f"[UDP] Request from {addr}, file_size={file_size}", COLOR_YELLOW)
                t = threading.Thread(target=handle_udp_request, args=(udp_sock, addr, file_size), daemon=True)
                t.start()
            except socket.timeout:
                pass

            # Handle TCP connections
            tcp_sock.settimeout(0.5)
            try:
                conn, caddr = tcp_sock.accept()
                cprint(f"[TCP] Accepted connection from {caddr}", COLOR_YELLOW)
                conn.settimeout(5.0)
                t = threading.Thread(target=handle_tcp_connection, args=(conn, caddr), daemon=True)
                t.start()
            except socket.timeout:
                pass

    except KeyboardInterrupt:
        cprint("[SERVER] Shutting down via KeyboardInterrupt...", COLOR_RED)
    finally:
        stop_event.set()
        bcast_thread.join(timeout=1.0)
        tcp_sock.close()
        udp_sock.close()


if __name__ == "__main__":
    main()