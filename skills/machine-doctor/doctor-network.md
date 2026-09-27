# macOS networking (`/machine-doctor network`)

Read this after platform detection when a Mac has connection failures, especially
when Claude Code cannot reach its API while other sites work. Run checks on the
affected Mac, not inside its Linux VM.

## Diagnose

Run the read-only helper from this skill's directory:

```bash
uv run tools/network_doctor.py
# Target another affected service or compare with a configured DNS server:
uv run tools/network_doctor.py --host api.anthropic.com --dns-server 100.100.100.100
```

The helper runs bounded checks concurrently: route, resolver/proxy configuration,
macOS host lookup, direct DNS, and HTTPS. It defaults to public DNS at `1.1.1.1`;
use a resolver from the captured configuration for private names or networks that
restrict public DNS. Use `100.100.100.100` only when Tailscale is active.

If the execution sandbox reports `Operation not permitted`, no DNS configuration,
or lookup failures, repeat with the tool's approved host-network access before
diagnosing the Mac. Sandbox failures are not evidence of a broken host network.

Interpret the evidence:

- `reachable`: normal HTTPS lookup and connection worked. A `401`, `403`, or `404`
  still proves an HTTP response was received; it does not prove login or API health.
- `resolver-mismatch`: direct DNS returned an address while curl could not resolve
  the hostname. Suspect a stale local cache or resolver routing. The helper also
  tries that freshly returned address with `curl --resolve`, preserving TLS hostname
  validation. A successful bypass strengthens the diagnosis; it changes no settings.
- `unresolved`: inspect the individual failures. DNS timeouts, TLS failures, and
  connection timeouts need different follow-up; do not prescribe cache resets for all.

If a proxy is configured, account for it: the bypass deliberately connects directly,
whereas normal curl may use environment proxy settings. Do not print proxy credentials.
Do not pin a diagnostic IP in `/etc/hosts` or disable a VPN based on this symptom alone.

## Recover a suspected stale macOS resolver cache

Explain the findings and that the next step flushes DNS caches and sends a reload
signal to `mDNSResponder`. Within an authorized repair request, run once using
non-interactive sudo:

```bash
sudo -n dscacheutil -flushcache
sudo -n killall -HUP mDNSResponder
```

If sudo requires a password, stop automated recovery and give the user these commands
to run in their own Terminal. Never ask them to send their password:

```bash
sudo dscacheutil -flushcache
sudo killall -HUP mDNSResponder
```

After completion, rerun the helper for the affected hostname three times. Confirm
normal DNS and HTTPS work, not just the bypass, then ask the user to retry the app.
If failures persist, report that the reset did not resolve them and investigate the
captured resolver/proxy routing instead of repeatedly flushing or claiming success.

## Duplicate-machine warnings

A duplicate computer-name or IP-address warning is a separate clue, not proof of
the DNS failure's cause. Get the exact warning before renaming the Mac, renewing its
address, or changing Tailscale identity. Routine mDNS log entries saying
`Duplicate question` do not establish a duplicate machine.

Observed in September 2026: multiple DNS servers returned `api.anthropic.com`, but
macOS lookup and curl failed immediately. HTTPS succeeded with a fresh DNS address
via `--resolve`. After the user ran the privileged cache reset, normal resolution
and three HTTPS checks succeeded. The duplicate-machine warning's cause was unconfirmed.
