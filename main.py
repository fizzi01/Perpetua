import argparse
import distutils
from distutils import util
from server import Server


def run_server(host, port, pos, logging, wait, screen_threshold, root, stdout=None, stderr=None):
    filtered_pos = pos.split(',')

    filtered_clients = {pos: {"conn": None, "addr": None} for pos in filtered_pos}

    s = Server(host=host, port=port, clients=filtered_clients, logging=logging, wait=wait,
               screen_threshold=screen_threshold, root=root, stdout=stdout)
    s.start()
    return s


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Start the server with given parameters.')
    parser.add_argument('--host', type=str, default="0.0.0.0", help='The host to start the server on.')
    parser.add_argument('--port', type=int, default=5001, help='The port to start the server on.')
    parser.add_argument('--pos', type=str, default="left", help='Specify the positions of the clients.')
    parser.add_argument('--ip', type=str, default="",
                        help='Specify the IP addresses of the clients.')
    parser.add_argument('--logging', type=lambda x: bool(distutils.util.strtobool(x)), default=True,
                        help='Enable or disable logging.')
    parser.add_argument('--wait', type=int, default=5, help='The time to wait for a connection.')
    parser.add_argument('--threshold', type=int, default=5, help='The threshold for screen transition.')
    args = parser.parse_args()

    positions = args.pos.split(',')
    ips = args.ip.split(',')
    if len(positions) != len(ips):
        print("The number of positions and IP addresses must be the same.")
        exit(1)

    clients = {pos: {"conn": None, "addr": ip} for pos, ip in zip(positions, ips)}

    server = Server(args.host, args.port, clients=clients, logging=args.logging, wait=args.wait,
                    screen_threshold=args.threshold)
    ret = server.start()
