"""
scripts/healthcheck.py
Healthcheck simple para liveness/readiness del contenedor.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
from urllib.parse import urlparse
from urllib.request import urlopen


def _http_ok(url: str, timeout: float = 3.0) -> bool:
    try:
        with urlopen(url, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 500) < 400
    except Exception:
        return False


def _tcp_ok(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _ldap_host() -> tuple[str, int]:
    raw = os.getenv("LDAP_SERVER", "")
    parsed = urlparse(raw if "://" in raw else f"ldap://{raw}")
    return parsed.hostname or "localhost", int(os.getenv("LDAP_PORT", parsed.port or 389))


def check_liveness() -> int:
    return 0 if _http_ok("http://localhost:8501/_stcore/health") else 1


def check_readiness() -> int:
    required = ["SS_HOST", "SS_PORT", "LDAP_SERVER"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        print(f"Missing env vars: {', '.join(missing)}")
        return 1

    if not _http_ok("http://localhost:8501/_stcore/health"):
        print("Streamlit health endpoint is down")
        return 1

    ss_host = os.getenv("SS_HOST", "")
    ss_port = int(os.getenv("SS_PORT", "3306"))
    if not _tcp_ok(ss_host, ss_port):
        print(f"SingleStore is unreachable at {ss_host}:{ss_port}")
        return 1

    ldap_host, ldap_port = _ldap_host()
    if not _tcp_ok(ldap_host, ldap_port):
        print(f"LDAP is unreachable at {ldap_host}:{ldap_port}")
        return 1

    hive_enabled = os.getenv("HIVE_ENABLED", "true").lower() == "true"
    hive_host = os.getenv("HIVE_HOST")
    if hive_enabled and hive_host:
        hive_port = int(os.getenv("HIVE_PORT", "10000"))
        if not _tcp_ok(hive_host, hive_port):
            print(f"Hive is unreachable at {hive_host}:{hive_port}")
            return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["liveness", "readiness"], default="liveness")
    args = parser.parse_args()

    if args.mode == "readiness":
        return check_readiness()
    return check_liveness()


if __name__ == "__main__":
    sys.exit(main())
