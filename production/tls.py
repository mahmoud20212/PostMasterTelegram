"""Certbot IP issuance/renewal and an unprivileged-to-Docker reload notification."""
import argparse
import ipaddress
import logging
import os
from pathlib import Path
import signal
import subprocess
import threading
import uuid

logger = logging.getLogger(__name__)
EVENT_DIRECTORY = Path("/run/cert-events")
DEPLOY_HOOK = "python /opt/onepost/tls.py signal"


def signal_reload(directory=EVENT_DIRECTORY):
    directory.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    temporary = directory / f"reload.{token}.tmp"
    temporary.write_text(token, encoding="ascii")
    temporary.replace(directory / "reload")


def issue_command(environ, staging=False):
    address = ipaddress.ip_address(environ.get("PUBLIC_IP", ""))
    if not address.is_global:
        raise ValueError("PUBLIC_IP must be publicly routable")
    email = environ.get("ACME_EMAIL", "")
    if not email or "@" not in email or email.endswith("@example.com"):
        raise ValueError("Set a real ACME_EMAIL for the certificate account")
    command = ["certbot", "certonly", "--non-interactive", "--agree-tos", "--email", email,
        "--cert-name", "onepost-ip", "--preferred-profile", "shortlived", "--webroot",
        "--webroot-path", "/var/www/acme", "--ip-address", str(address), "--deploy-hook", DEPLOY_HOOK]
    if staging:
        # Do not install an untrusted staging certificate into Nginx's live path,
        # nor let Certbot reuse it when production issuance is requested later.
        command.extend(["--staging", "--config-dir", "/etc/letsencrypt/staging",
            "--work-dir", "/tmp/letsencrypt-staging", "--logs-dir", "/tmp/letsencrypt-staging-logs"])
    return command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["issue", "renew-loop", "signal"])
    parser.add_argument("--staging", action="store_true", help="Use Let's Encrypt staging; its certificate is NOT browser-trusted")
    args = parser.parse_args()
    if args.action == "signal":
        signal_reload()
        return
    if args.action == "issue":
        try:
            command = issue_command(os.environ, args.staging)
        except ValueError as error:
            parser.error(str(error))
        subprocess.run(command, check=True)
        return
    logging.basicConfig(level=logging.INFO)
    stopped = threading.Event()
    child = None

    def terminate(*_):
        stopped.set()
        if child is not None and child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    while not stopped.is_set():
        child = subprocess.Popen(["certbot", "renew", "--non-interactive", "--deploy-hook", DEPLOY_HOOK])
        result = child.wait()
        if result and not stopped.is_set():
            logger.error("Certificate renewal failed; inspect certificate-renewal logs before certificate expiry")
        child = None
        stopped.wait(12 * 60 * 60)


if __name__ == "__main__":
    main()
