from server import Server


if __name__ == "__main__":
    server = Server("0.0.0.0", 5001, logging=True)
    ret = server.start()

