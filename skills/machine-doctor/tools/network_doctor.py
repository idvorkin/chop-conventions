#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["typer>=0.12"]
# ///
"""Read-only macOS DNS and HTTPS checks; JSON output retains diagnostic evidence."""

import ipaddress
import json
import platform
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor


def probe(argv):
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=15, check=False
        )
        return {
            "code": result.returncode,
            "output": result.stdout.strip(),
            "error": result.stderr.strip(),
        }
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"code": -1, "output": "", "error": str(error)}


def ipv4_answers(output):
    addresses = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 5 and fields[3] == "A":
            try:
                addresses.append(str(ipaddress.IPv4Address(fields[4])))
            except ipaddress.AddressValueError:
                pass
    return addresses


def classify(https, dns):
    if https["code"] == 0 and re.fullmatch(r"[1-5][0-9]{2}", https["output"]):
        return (
            "reachable",
            "DNS and TLS/HTTP worked; HTTP status does not prove app authentication or health.",
        )
    if https["code"] == 6 and dns["code"] == 0 and ipv4_answers(dns["output"]):
        return (
            "resolver-mismatch",
            "Direct DNS answers but the application lookup fails; local cache or resolver routing is suspect.",
        )
    return (
        "unresolved",
        "Inspect probe errors; do not assume a stale cache or successful connectivity.",
    )


def diagnose(host, server):
    curl = [
        "/usr/bin/curl",
        "--silent",
        "--show-error",
        "--connect-timeout",
        "5",
        "--max-time",
        "12",
        "--output",
        "/dev/null",
        "--write-out",
        "%{http_code}",
    ]
    commands = {
        "resolver_config": ["/usr/sbin/scutil", "--dns"],
        "proxy_config": ["/usr/sbin/scutil", "--proxy"],
        "default_route": ["/sbin/route", "-n", "get", "default"],
        "system_lookup": ["/usr/bin/dscacheutil", "-q", "host", "-a", "name", host],
        "direct_dns": [
            "/usr/bin/dig",
            "+time=2",
            "+tries=1",
            "+noall",
            "+answer",
            f"@{server}",
            host,
            "A",
        ],
        "https": [*curl, f"https://{host}/"],
    }
    with ThreadPoolExecutor(max_workers=len(commands)) as pool:
        results = dict(zip(commands, pool.map(probe, commands.values())))
    status, detail = classify(results["https"], results["direct_dns"])
    if status == "resolver-mismatch":
        address = ipv4_answers(results["direct_dns"]["output"])[0]
        results["https_bypass"] = probe(
            [
                *curl,
                "--noproxy",
                "*",
                "--resolve",
                f"{host}:443:{address}",
                f"https://{host}/",
            ]
        )
    return {
        "host": host,
        "dns_server": server,
        "status": status,
        "detail": detail,
        "probes": results,
    }


def _build_app():
    import typer
    from rich import print as rich_print

    app = typer.Typer()

    @app.command()
    def main(host: str = "api.anthropic.com", dns_server: str = "1.1.1.1"):
        """Compare Mac application lookup with direct DNS, without changing settings."""
        if platform.system() != "Darwin":
            rich_print("[red]Run this diagnostic on the affected Mac host.[/red]")
            raise typer.Exit(2)
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", host):
            raise typer.BadParameter("Use a DNS hostname, without a URL or port.")
        try:
            ipaddress.ip_address(dns_server)
        except ValueError:
            raise typer.BadParameter("DNS server must be an IP address.") from None
        report = diagnose(host, dns_server)
        print(json.dumps(report, indent=2))
        raise typer.Exit(0 if report["status"] == "reachable" else 1)

    return app


if __name__ == "__main__":
    _build_app()()
