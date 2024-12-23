import sys

from launcher import ApplicationLauncher


def main():
    app = ApplicationLauncher()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
