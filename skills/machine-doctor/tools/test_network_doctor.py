import subprocess

from network_doctor import classify, ipv4_answers, probe


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
