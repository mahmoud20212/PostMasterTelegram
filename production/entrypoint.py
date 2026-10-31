import os
import sys

from production.runtime import load_environment


def main():
    try:
        load_environment()
    except ValueError as error:
        raise SystemExit(str(error)) from None
    command = sys.argv[1:]
    if not command:
        raise SystemExit("A process command is required")
    # Do not leave a shell/Python wrapper between Docker SIGTERM and the process.
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
