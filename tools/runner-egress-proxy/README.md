# runner-egress-proxy — terminating-TLS egress proxy (staged lane)

Implements the W3 build of ADR-024 (option (b) of the NFM-5582 CTO ruling,
parent NFM-5583): a loopback terminating-TLS MITM egress proxy for the
wenjiedeMac-Studio GHA runner `production`. The DPI on this host's path is
ClientHello-sensitive — the .NET broker's ClientHello is reset while
CPython's passes — so TLS terminates locally and is **re-originated with
CPython's ssl stack**. A CONNECT tunnel would not help (same ClientHello
end-to-end) and is not used.

**Staged, not active.** Building and arming this daemon never changes runner
egress: the runner stays on DIRECT. Activation is SRE-gated by the
pre-authorized ADR-024 triggers (see `docs/adr/ADR-024-*.md` §4).

## Hardening bar mapping (G1–G4)

| Guardrail | Where |
| --- | --- |
| **G1** loopback-only bind | `egress_proxy.py` `make_server()` refuses any bind != 127.0.0.1; plist pins `NFM_EGRESS_BIND=127.0.0.1` |
| **G2** name-constrained MITM CA, ≤30d, key destroyed post-negative-test, login-keychain `-p ssl` only | `mint_ca.sh` + `nc_der.py` |
| **G3** upstream full verification, fail-closed, no verify-off | `egress_proxy.py` `upstream_ssl_context()` (`create_default_context`, no `CERT_NONE` anywhere); verify failure ⇒ `UpstreamVerifyFailed`, no connect-through |
| **G4** dest-class allowlist, deny-all-else | `egress_proxy.py` `ALLOWED_DNS_SUFFIXES` (github.com, actions.githubusercontent.com) checked at CONNECT → 403; non-CONNECT and non-443 also denied; in-class-but-no-static-leaf → fail-closed 502 (ca.key is gone, nothing can mint) |

## Files

- `egress_proxy.py` — the proxy (stdlib only; pip/uv cannot reach pypi from
  this host). CONNECT → allowlist gate → local TLS termination with the
  static leaf → re-originated upstream TLS → byte pump.
- `mint_ca.sh` — mints the 30d name-constrained CA, static leaves for the
  enumerated dest hosts, and three out-of-scope probe leaves for the negative
  tests. Refuses to overwrite an existing CA; refuses out-of-class leaves.
- `nc_der.py` — builds the NAME_CONSTRAINTS extension as raw DER (OpenSSL's
  config parser cannot express DirName subtrees there). See its docstring for
  the measured DirName delta from the bar text.
- `com.nfm.runner-egress-proxy.plist` — launchd unit (RunAtLoad + KeepAlive).
- `test_mint_ca.py`, `test_egress_proxy.py` — pytest; `python3 -m pytest .`
  (stdlib + openssl CLI only).

**No secrets in this repo.** All key material stays host-local under
`~/nfm-egress-proxy`; only public tooling and config live here.

## Staging sequence (host-side, evidenced on the issue)

The order is load-bearing (ADR-024 G2 build note): the negative tests need
`ca.key` (they mint probes), and `ca.key` must be destroyed only after they
pass — enrollment happens with a keyless CA.

```sh
# 1. mint (into ~/nfm-egress-proxy; refuses overwrite)
bash tools/runner-egress-proxy/mint_ca.sh ~/nfm-egress-proxy

# 2. NEGATIVE TESTS — all three probes MUST be rejected (openssl + Apple SecTrust)
openssl verify -CAfile ~/nfm-egress-proxy/ca.cert.pem ~/nfm-egress-proxy/probe.crt
openssl verify -CAfile ~/nfm-egress-proxy/ca.cert.pem ~/nfm-egress-proxy/probe-dirname.crt
openssl verify -CAfile ~/nfm-egress-proxy/ca.cert.pem ~/nfm-egress-proxy/probe-san.crt
security verify-cert -c ~/nfm-egress-proxy/probe.crt \
  -c ~/nfm-egress-proxy/ca.cert.pem -p ssl
security verify-cert -c ~/nfm-egress-proxy/probe-dirname.crt \
  -c ~/nfm-egress-proxy/ca.cert.pem -p ssl
security verify-cert -c ~/nfm-egress-proxy/probe-san.crt \
  -c ~/nfm-egress-proxy/ca.cert.pem -p ssl
# expect: "permitted subtree violation" / CSSMERR_TP_INVALID_CERTIFICATE

# 3. destroy the CA key + probe keys (only after the tests pass)
rm -P ~/nfm-egress-proxy/ca.key ~/nfm-egress-proxy/probe.key \
      ~/nfm-egress-proxy/probe-dirname.key ~/nfm-egress-proxy/probe-san.key

# 4. enroll the CA — login keychain, ssl policy ONLY (never System, no sudo)
security add-trusted-cert -p ssl \
  -k ~/Library/Keychains/login.keychain-db ~/nfm-egress-proxy/ca.cert.pem

# 5. verify trust (dump-trust-settings, NOT find-certificate: import != trust)
security dump-trust-settings
security dump-trust-settings -d

# 6. stage the daemon (loopback, KeepAlive)
cp tools/runner-egress-proxy/egress_proxy.py ~/nfm-egress-proxy/
cp tools/runner-egress-proxy/com.nfm.runner-egress-proxy.plist \
   ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) \
   ~/Library/LaunchAgents/com.nfm.runner-egress-proxy.plist
launchctl print gui/$(id -u)/com.nfm.runner-egress-proxy | grep -E "KeepAlive|state"
lsof -nP -iTCP:8443 -sTCP:LISTEN   # must show 127.0.0.1:8443 only
```

The runner is NOT pointed at the proxy by any of the above — no `.proxy`
file, no proxy env vars. That step belongs to W4 controlled cutover.

## Kill-switch (W4 owns verification; stated here for reviewers)

Single step back to direct egress: remove the proxy env vars from the runner
service (W4's cutover adds them; until then the runner never had them) and,
if fully standing down, `launchctl bootout gui/$(id -u)/com.nfm.runner-egress-proxy`
plus `security remove-trusted-cert ...` for the CA.
