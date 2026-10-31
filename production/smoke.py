"""Opt-in Docker integration test. Uses a unique project, synthetic credentials and no Telegram network calls."""
import argparse
import http.client
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import time
import uuid

from production.setup import create_configuration

ROOT = Path(__file__).resolve().parent.parent


def unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def https_request(port, context):
    with socket.create_connection(("127.0.0.1", port), timeout=3) as raw:
        with context.wrap_socket(raw, server_hostname="8.8.8.8") as stream:
            certificate = stream.getpeercert(binary_form=True)
            stream.sendall(b"GET /admin/login/ HTTP/1.1\r\nHost: 8.8.8.8\r\nConnection: close\r\n\r\n")
            response = http.client.HTTPResponse(stream)
            response.begin()
            response.read()
            return response.status, certificate


def wait_for(check, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (OSError, ssl.SSLError, http.client.HTTPException):
            pass
        time.sleep(1)
    raise RuntimeError("Smoke check timed out")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="Build isolated images before testing")
    args = parser.parse_args()
    # Do not create configuration/start anything if the daemon is unavailable.
    try:
        subprocess.run(["docker", "info"], check=True, stdout=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        raise SystemExit("Docker daemon unavailable: smoke test not run; no test containers or configuration created.") from None
    project = "onepost-smoke-" + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix="onepost-smoke-") as directory:
        root = Path(directory)
        env_file = create_configuration(root, "8.8.8.8", "smoke@onepost.test", "123456:" + "x" * 35, "12345", "a" * 32)
        http_port, tls_port = unused_port(), unused_port()
        env = dict(os.environ, PUBLIC_IP="8.8.8.8", ACME_EMAIL="smoke@onepost.test",
            ONEPOST_SECRETS_DIR=str(root / ".production/secrets"), ONEPOST_HTTP_BIND="127.0.0.1",
            ONEPOST_HTTP_PORT=str(http_port), ONEPOST_HTTPS_PORT=str(tls_port),
            APP_IMAGE=f"onepost-smoke:{project}", PROXY_IMAGE=f"onepost-nginx-smoke:{project}",
            CERTBOT_IMAGE=f"onepost-certbot-smoke:{project}")
        override = root / "override.json"
        override.write_text(json.dumps({"services": {
            "web": {"environment": {"ONEPOST_SMOKE_PROJECT": project}},
            "worker": {"environment": {"ONEPOST_SMOKE_PROJECT": project}, "command": ["celery", "-A", "production.smoke_tasks:app",
                "worker", "-l", "INFO", "--pool=prefork", "--concurrency=2", "-Q", "publishing,notifications,celery"]},
            "fixture": {"image": env["APP_IMAGE"], "user": "0:0", "entrypoint": ["python", "-m", "production.smoke_certificate"],
                "environment": {"PUBLIC_IP": "8.8.8.8", "ONEPOST_SMOKE_PROJECT": project},
                "volumes": ["certificates:/etc/letsencrypt", "certificate-events:/run/cert-events"]},
        }}), encoding="utf-8")
        prefix = ["docker", "compose", "-p", project, "--env-file", str(env_file), "-f", str(ROOT / "docker-compose.production.yml"), "-f", str(override)]

        def dc(*command, capture=False):
            return subprocess.run(prefix + list(command), env=env, check=True, text=True, capture_output=capture).stdout

        def probe(action):
            dc("exec", "-T", "web", "python", "-m", "production.entrypoint", "python", "-m", "production.smoke_probe", action)

        try:
            if args.build:
                dc("--profile", "ops", "build", "web", "proxy", "certbot")
            dc("up", "-d", "--wait", "--wait-timeout", "120", "proxy", "worker")
            connection = http.client.HTTPConnection("127.0.0.1", http_port, timeout=5)
            connection.request("GET", "/admin/login/", headers={"Host": "8.8.8.8"})
            assert connection.getresponse().status == 503
            connection.close()
            ca = dc("run", "--rm", "--no-deps", "fixture", capture=True)
            ca_path = root / "test-ca.pem"
            ca_path.write_text(ca, encoding="ascii")
            context = ssl.create_default_context(cafile=str(ca_path))
            def ready_certificate():
                try:
                    status, certificate = https_request(tls_port, context)
                    print(f"HTTPS status: {status}", flush=True)
                    return certificate if status == 200 else None
                except Exception as error:
                    print(
                        f"HTTPS error: {type(error).__name__}: {error}",
                        flush=True
                    )
                    return None

            
            original = wait_for(ready_certificate)

            def redirect_ready():
                connection = http.client.HTTPConnection(
                    "127.0.0.1", http_port, timeout=5
                )
                try:
                    connection.request(
                        "GET",
                        "/admin/login/",
                        headers={"Host": "8.8.8.8"},
                    )
                    response = connection.getresponse()
                    status = response.status
                    location = response.getheader("Location")
                    response.read()

                    print(
                        f"HTTP redirect: {status} Location: {location}",
                        flush=True,
                    )

                    return (
                        status == 308
                        and location == "https://8.8.8.8/admin/login/"
                    )
                finally:
                    connection.close()

            wait_for(redirect_ready, timeout=30)

            assert dc("exec", "-T", "web", "id", "-u", capture=True).strip() == "10001", "Application runs as root"
            probe("prepare")
            dc("run", "--rm", "--no-deps", "fixture", capture=True)
            wait_for(lambda: (response := https_request(tls_port, context))[0] == 200 and response[1] != original)
            dc("up", "-d", "--force-recreate", "--wait", "db", "redis")
            dc("up", "-d", "--no-deps", "--force-recreate", "--wait", "web", "worker")
            probe("verify")
            dc("run", "--rm", "--no-deps", "scheduler", "python", "manage.py", "run_publishing_scheduler", "--once")
            print("Isolated Docker smoke tests passed. No live bot or Telegram sender was started.")
        finally:
            # The only volume removal in this tooling targets this freshly-created
            # random test project, never the production Compose project.
            assert project.startswith("onepost-smoke-")
            dc("down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    main()
