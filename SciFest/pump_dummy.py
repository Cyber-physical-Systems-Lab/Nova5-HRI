import socket
import time

def start_server(host='192.168.0.152', port=8888):
    # Create a TCP/IP socket
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind((host, port))
    server_socket.listen(1)

    print(f"Server listening on {host}:{port}...")

    conn, addr = server_socket.accept()
    print(f"Connected by {addr}")

    buffer = ""
    try:
        while True:
            data = conn.recv(1024)
            if not data:
                print("Client disconnected.")
                break
            buffer += data.decode('utf-8')
            while '\n' in buffer:
                message, buffer = buffer.split('\n', 1)
                message = message.strip()
                print(f"Received: {message}")
                if message == "deliver a new sticker":
                    print("Starting sticker delivery sequence...")
                    conn.sendall(b"pickup reached\n")
                    print("Sent: pickup reached")
                    time.sleep(2)
                    conn.sendall(b"drop reached\n")
                    print("Sent: drop reached")
                    time.sleep(2)
                    conn.sendall(b"one sticker finished\n")
                    print("Sent: one sticker finished")
                elif message == "wait until next sticker":
                    print("Waiting for next sticker...")
                else:
                    print(f"Unknown message: {message}")
    except KeyboardInterrupt:
        print("Server stopped manually.")
    finally:
        conn.close()
        server_socket.close()

if __name__ == "__main__":
    start_server()
