import os
from urllib.request import Request, urlopen


def main():
    address = os.environ["PUBLIC_IP"]
    host = f"[{address}]" if ":" in address else address
    request = Request("http://127.0.0.1:8000/admin/login/", headers={"Host": host, "X-Forwarded-Proto": "https"})
    with urlopen(request, timeout=3) as response:
        if response.status != 200:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
