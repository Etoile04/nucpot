#!/usr/bin/env python3
"""NAME_CONSTRAINTS extension DER builder for mint_ca.sh (ADR-024 G2).

OpenSSL's config parser cannot express DirName entries inside
nameConstraints (v2i_GENERAL_NAME_ex rejects them), so the extension is
assembled as raw DER and injected with `nameConstraints = critical, DER:<hex>`.

Encoded bar (G2), measured on this host 2026-10-18 (evidence on NFM-5586):
  permitted: DNS:github.com, DNS:actions.githubusercontent.com,
             DirName:/O=NFM Egress NC Block,
             email/URI reserved host (see below)
  excluded:  IP:0.0.0.0/0.0.0.0, IP:::/::

DirName delta from the bar's literal `excluded;DirName:/` (empty DN):
an empty-DN excluded subtree constrains the directoryName form everywhere,
INCLUDING leaf subject fields, so it forces subject-less leaves — which
Apple SecTrust rejects for TLS ("Invalid empty SubjectName"). Measured:
empty-DN variant => CSSMERR_TP_INVALID_CERTIFICATE / openssl error 47 on any
leaf with a subject. The reserved permitted;DirName subtree below is the
strongest form BOTH consumers accept: every subject or SAN directoryName
outside /O=NFM Egress NC Block violates the permitted subtree (measured:
openssl "permitted subtree violation", Apple INVALID_CERTIFICATE), while
in-scope leaves verify (openssl OK; Apple NOT_TRUSTED pre-enrollment only).

Email/URI delta from the bar's `excluded;email:.` / `excluded;URI:.`:
exclude-all forms are NOT enforced by this stack. Measured on OpenSSL 3.6.4
(and identically on Apple SecTrust): a leaf carrying
email:anyone@evil.example.com + URI:https://evil.example.com/x verifies OK
under excluded email:"."/URI:"." AND under the RFC 5280 empty-string form,
while concrete-hostname exclusions (email:evil.example.com) DO reject
(openssl error 48). So exclude-all cannot be expressed in the excluded
list; instead a reserved permitted email/URI host is encoded — the same
reserved-subtree pattern as DirName — and that IS enforced: the evil
email/URI leaf fails with openssl error 47 ("permitted subtree violation")
and Apple INVALID_CERTIFICATE, while in-scope leaves still verify.

GeneralName tags (RFC 5280): dNSName [2] primitive, rfc822Name [1]
primitive, URI [6] primitive, iPAddress [7] OCTET addr+mask,
directoryName [4] carrying the full Name DER.
"""

import ipaddress

PERMITTED_DNS = ("github.com", "actions.githubusercontent.com")
RESERVED_DIRNAME_BLOCK_ORG = "NFM Egress NC Block"
RESERVED_EMAIL_URI_HOST = "nc-block.reserved.nfm.invalid"


def _tlv(tag, content):
    length = len(content)
    if length < 0x80:
        len_bytes = bytes([length])
    else:
        raw = length.to_bytes((length.bit_length() + 7) // 8, "big")
        len_bytes = bytes([0x80 | len(raw)]) + raw
    return bytes([tag]) + len_bytes + content


def _subtree(base):
    return _tlv(0x30, base)


def dns(name):
    return _tlv(0x82, name.encode("ascii"))


def email(name):
    return _tlv(0x81, name.encode("ascii"))  # rfc822Name


def uri(name):
    return _tlv(0x86, name.encode("ascii"))


def ip(addr, mask):
    a = ipaddress.ip_address(addr).packed
    m = ipaddress.ip_address(mask).packed
    if len(a) != len(m):
        raise ValueError("address/mask family mismatch: %s %s" % (addr, mask))
    return _tlv(0x87, a + m)


def _ava(oid_bytes, value):
    # AttributeTypeAndValue ::= SEQUENCE { type OID, value string }
    return _tlv(0x30, _tlv(0x06, bytes(oid_bytes)) + _tlv(0x0C, value.encode("utf-8")))


def dirname_reserved_block():
    """[4] GeneralName wrapping Name = one RDN: O=NFM Egress NC Block."""
    org_oid = (0x55, 0x04, 0x0A)  # 2.5.4.10 = organizationName (O)
    rdn = _tlv(0x31, _ava(org_oid, RESERVED_DIRNAME_BLOCK_ORG))  # SET
    name = _tlv(0x30, rdn)  # RDNSequence
    return _tlv(0xA4, name)


def name_constraints_der(permitted_dns=PERMITTED_DNS):
    permitted = b"".join(_subtree(dns(n)) for n in permitted_dns)
    permitted += _subtree(dirname_reserved_block())
    permitted += _subtree(email(RESERVED_EMAIL_URI_HOST))
    permitted += _subtree(uri(RESERVED_EMAIL_URI_HOST))
    excluded = b"".join(
        _subtree(base)
        for base in (
            ip("0.0.0.0", "0.0.0.0"),
            ip("::", "::"),
        )
    )
    body = _tlv(0xA0, permitted) + _tlv(0xA1, excluded)
    return _tlv(0x30, body)


if __name__ == "__main__":
    print(name_constraints_der().hex().upper())
