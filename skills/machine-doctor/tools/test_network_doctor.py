import subprocess

import network_doctor
from network_doctor import classify, diagnose, ipv4_answers, probe, valid_hostname


def result(code=0, output=""):
    return {"code": code, "output": output, "error": ""}


def test_dns_mismatch():
    dns = result(output="api.example.com. 60 IN A 192.0.2.1")
    assert classify(result(6), dns)[0] == "resolver-mismatch"
    assert classify(result(6), result())[0] == "unresolved"
    assert classify(result(28), dns)[0] == "unresolved"


def test_http_errors_still_prove_connectivity():
    for status in ["200", "401", "403", "404", "503"]:
        assert classify(result(output=status), result())[0] == "reachable"
    assert classify(result(output="000"), result())[0] == "unresolved"
    assert classify(result(60, "200"), result())[0] == "unresolved"


def test_only_actual_a_records_are_used():
    assert ipv4_answers(
        "api.example.com. 60 IN CNAME edge.example.com.\n"
        "edge.example.com. 60 IN A 192.0.2.2\n"
        "bad.example.com. 60 IN A invalid"
    ) == ["192.0.2.2"]


def test_timeout_is_reported(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 15)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert probe(["fake-command"])["code"] == -1


def fake_probes(monkeypatch, https, dig="", bypass="200"):
    calls = []

    def fake(argv):
        calls.append(argv)
        if argv[0] == "/usr/bin/dig":
            return result(output=dig)
        if argv[0] == "/usr/bin/curl":
            return result(0, bypass) if "--resolve" in argv else https
        return result()

    monkeypatch.setattr(network_doctor, "probe", fake)
    return calls


def test_diagnose_reachable_skips_bypass(monkeypatch):
    calls = fake_probes(monkeypatch, result(output="401"))
    report = diagnose("api.example.com", "192.0.2.53")
    assert report["status"] == "reachable"
    assert "https_bypass" not in report["probes"]
    assert [
        "/usr/bin/dig",
        "+time=2",
        "+tries=1",
        "+noall",
        "+answer",
        "@192.0.2.53",
        "api.example.com",
        "A",
    ] in calls
    assert len(calls) == 6


def test_diagnose_mismatch_bypasses_with_fresh_address(monkeypatch):
    calls = fake_probes(
        monkeypatch, result(6), dig="api.example.com. 60 IN A 192.0.2.7"
    )
    report = diagnose("api.example.com", "192.0.2.53")
    assert report["status"] == "resolver-mismatch"
    assert report["probes"]["https_bypass"]["output"] == "200"
    bypass = next(argv for argv in calls if "--resolve" in argv)
    assert bypass[bypass.index("--resolve") + 1] == "api.example.com:443:192.0.2.7"
    assert bypass[bypass.index("--noproxy") + 1] == "*"
    assert bypass[-1] == "https://api.example.com/"


def test_hostname_labels_are_validated():
    assert valid_hostname("api.anthropic.com")
    assert valid_hostname("localhost")
    for bad in [
        "foo..bar",
        "foo.-bar",
        "foo-.bar",
        ".foo",
        "a_b.com",
        "https://x.com",
        "x.com:443",
        "a" * 64 + ".com",
        ("a" * 63 + ".") * 4 + "com",
    ]:
        assert not valid_hostname(bad), bad
