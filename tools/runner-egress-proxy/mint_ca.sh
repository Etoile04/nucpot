#!/bin/bash
# NFM runner-egress-proxy CA minter — ADR-024 G2 hardened bar.
#
# Mints into <install-dir> (default ~/nfm-egress-proxy):
#   ca.cert.pem            30d CA, name-constrained on ALL name types, pathlen:0
#   ca.key                 CA private key (NOT destroyed here — destruction is
#                          a separate evidenced step AFTER the negative test)
#   leaf.<host>.crt/.key   static leaves for the allowlisted dest classes
#   leaf.<host>.chain.crt  leaf + CA, what the proxy serves
#   probe.crt/.key         OUT-OF-SCOPE leaf for the SecTrust negative test
#
# The minter refuses to sign any leaf outside the G4 dest classes except the
# explicit probe. NO secrets ever leave the install dir.
set -euo pipefail

umask 077

INSTALL_DIR="${1:-$HOME/nfm-egress-proxy}"
PROBE_HOST="${2:-out-of-scope.probe.nfm.invalid}"
CA_DAYS=30
LEAF_DAYS=29

# Dest hosts enumerated from runner `production` logs (NFM-5586, 10-18).
LEAF_HOSTS=(
  broker.actions.githubusercontent.com
  pipelinesghubeus23.actions.githubusercontent.com
  github.com
  vstoken.actions.githubusercontent.com
)

ALLOWED_SUFFIXES=(github.com actions.githubusercontent.com)
host_in_class() {
  local h s
  h=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')
  case "$h" in *.) h="${h%.}" ;; esac
  for s in "${ALLOWED_SUFFIXES[@]}"; do
    if [[ "$h" == "$s" || "$h" == *."$s" ]]; then return 0; fi
  done
  return 1
}

mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

if [[ -e ca.cert.pem ]]; then
  echo "refusing to overwrite existing CA at $INSTALL_DIR/ca.cert.pem" >&2
  exit 1
fi

# OpenSSL 3.6's config parser cannot express DirName inside nameConstraints,
# so the extension is injected as raw DER (see nc_der.py for the encoding).
NC_HEX=$(python3 "$(dirname "$0")/nc_der.py")

cat > openssl-ca.cnf <<EOF
[req]
distinguished_name = ca_dn
x509_extensions = v3_ca
prompt = no

[ca_dn]
O = Nuclear Fuel Materials Database
OU = runner-egress-proxy
CN = NFM runner egress proxy CA (staged lane)

[v3_ca]
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always,issuer
basicConstraints = critical, CA:TRUE, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
nameConstraints = critical, DER:$NC_HEX
EOF

openssl genrsa -out ca.key 4096 2>/dev/null
openssl req -x509 -new -sha256 -days "$CA_DAYS" \
  -key ca.key -config openssl-ca.cnf -extensions v3_ca -out ca.cert.pem

leaf_ext() { # $1 = host
  cat <<EOF
[leaf]
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:$1
EOF
}

mint_leaf() { # $1 = host — refuses out-of-class hosts (G4-in-mint)
  local h="$1"
  if ! host_in_class "$h"; then
    echo "refusing to mint leaf for out-of-class host: $h" >&2
    exit 1
  fi
  leaf_ext "$h" > "leaf.$h.ext"
  openssl genrsa -out "leaf.$h.key" 2048 2>/dev/null
  # Subject sits under the CA's reserved permitted;DirName subtree —
  # see nc_der.py for why the leaf DN cannot live outside it.
  openssl req -new -sha256 -key "leaf.$h.key" \
    -subj "/O=NFM Egress NC Block/OU=runner-egress-proxy/CN=$h" \
    -out "leaf.$h.csr"
  openssl x509 -req -sha256 -days "$LEAF_DAYS" \
    -in "leaf.$h.csr" -CA ca.cert.pem -CAkey ca.key \
    -extfile "leaf.$h.ext" -extensions leaf -out "leaf.$h.crt" 2>/dev/null
  cat "leaf.$h.crt" ca.cert.pem > "leaf.$h.chain.crt"
  rm -f "leaf.$h.csr" "leaf.$h.ext"
}

for h in "${LEAF_HOSTS[@]}"; do
  mint_leaf "$h"
done

# Out-of-scope probe leaves — signed by the same CA, exist ONLY for the
# SecTrust negative tests (an out-of-scope leaf MUST be rejected).
# Both keys are destroyed together with ca.key after the tests pass.
if host_in_class "$PROBE_HOST"; then
  echo "probe host must be OUT of the allowlist classes: $PROBE_HOST" >&2
  exit 1
fi

# probe 1: out-of-class DNS SAN (+ excluded-IP SAN)
cat > probe.ext <<EOF
[leaf]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:$PROBE_HOST, IP:198.51.100.1
EOF
openssl genrsa -out probe.key 2048 2>/dev/null
openssl req -new -sha256 -key probe.key \
  -subj "/O=NFM Egress NC Block/CN=$PROBE_HOST" \
  -out probe.csr
openssl x509 -req -sha256 -days "$LEAF_DAYS" \
  -in probe.csr -CA ca.cert.pem -CAkey ca.key \
  -extfile probe.ext -extensions leaf -out probe.crt 2>/dev/null
rm -f probe.csr probe.ext

# probe 2: in-class SAN but subject DN outside the reserved DirName subtree
cat > probe-dirname.ext <<EOF
[leaf]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:github.com
EOF
openssl genrsa -out probe-dirname.key 2048 2>/dev/null
openssl req -new -sha256 -key probe-dirname.key \
  -subj "/O=Evil Corp/CN=dirname-probe" \
  -out probe-dirname.csr
openssl x509 -req -sha256 -days "$LEAF_DAYS" \
  -in probe-dirname.csr -CA ca.cert.pem -CAkey ca.key \
  -extfile probe-dirname.ext -extensions leaf -out probe-dirname.crt 2>/dev/null
rm -f probe-dirname.csr probe-dirname.ext

echo "minted in $INSTALL_DIR:"
echo "  ca.cert.pem        (${CA_DAYS}d, name-constrained all name types, pathlen:0)"
echo "  ca.key             (kept for the negative tests; destroy AFTER they pass)"
echo "  leaves             ${LEAF_HOSTS[*]}"
echo "  probe.crt          (out-of-class DNS SAN — negative test 1)"
echo "  probe-dirname.crt  (foreign subject DN — negative test 2)"
echo "NEXT — in order, each evidenced on the issue:"
echo "  1. negative tests: BOTH probes MUST be rejected (SecTrust / openssl verify)"
echo "  2. destroy ca.key ca.srl probe.key probe-dirname.key (rm -P)"
echo "  3. enroll ca.cert.pem login-keychain -p ssl ONLY (never System, no sudo)"
