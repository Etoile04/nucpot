"""Tests for mint_ca.sh — hardened MITM CA per ADR-024 G2.

Bar: name constraints on ALL name types, validity <=30d, pathlen:0,
static leaves for the allowlisted dest classes, out-of-scope probe leaf,
ca.key survives minting (destruction is a separate post-negative-test step).
"""

import datetime
import os
import pathlib
import subprocess

import pytest

HERE = pathlib.Path(__file__).resolve().parent
MINT = HERE / "mint_ca.sh"

DEFAULT_HOSTS = (
    "broker.actions.githubusercontent.com",
    "pipelinesghubeus23.actions.githubusercontent.com",
    "github.com",
    "vstoken.actions.githubusercontent.com",
)
PROBE_HOST = "out-of-scope.probe.nfm.invalid"


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **kw)


@pytest.fixture(scope="module")
def minted(tmp_path_factory):
    """One mint run shared by the read-only assertions."""
    install = tmp_path_factory.mktemp("install")
    r = run(["bash", str(MINT), str(install), PROBE_HOST])
    assert r.returncode == 0, r.stderr
    return install


def ca_text(minted):
    return run(
        ["openssl", "x509", "-in", str(minted / "ca.cert.pem"), "-noout", "-text"]
    ).stdout


class TestCaNameConstraints:
    def test_extension_is_critical(self, minted):
        assert "X509v3 Name Constraints: critical" in ca_text(minted)

    def test_permitted_dns_subtrees(self, minted):
        text = ca_text(minted)
        assert "DNS:github.com" in text
        assert "DNS:actions.githubusercontent.com" in text

    def test_permitted_dirname_reserved_subtree(self, minted):
        # Bar delta (measured, see nc_der.py): reserved permitted subtree
        # instead of the empty-DN excluded form, which both Apple SecTrust
        # and OpenSSL reject for any leaf with a subject DN.
        assert "DirName:O = NFM Egress NC Block" in ca_text(minted)

    def test_excluded_ipv4_all(self, minted):
        assert "IP:0.0.0.0/0.0.0.0" in ca_text(minted)

    def test_excluded_ipv6_all(self, minted):
        assert "IP:0:0:0:0:0:0:0:0/0:0:0:0:0:0:0:0" in ca_text(minted)

    def test_excluded_email_all(self, minted):
        assert "email:." in ca_text(minted)

    def test_excluded_uri_all(self, minted):
        assert "URI:." in ca_text(minted)


class TestCaShape:
    def test_basic_constraints_ca_true_pathlen0_critical(self, minted):
        text = ca_text(minted)
        assert "X509v3 Basic Constraints: critical" in text
        assert "CA:TRUE, pathlen:0" in text

    def test_key_usage_certificate_sign_critical(self, minted):
        text = ca_text(minted)
        assert "X509v3 Key Usage: critical" in text
        assert "Certificate Sign" in text

    def test_validity_at_most_30d(self, minted):
        r = run(
            [
                "openssl", "x509", "-in", str(minted / "ca.cert.pem"), "-noout",
                "-startdate", "-enddate",
            ]
        )
        lines = dict(
            l.split("=", 1) for l in r.stdout.strip().splitlines() if "=" in l
        )
        fmt = "%b %d %H:%M:%S %Y %Z"
        nb = datetime.datetime.strptime(lines["notBefore"], fmt)
        na = datetime.datetime.strptime(lines["notAfter"], fmt)
        assert (na - nb) <= datetime.timedelta(days=30, minutes=5)

    def test_ca_key_exists_after_mint(self, minted):
        """Destruction happens AFTER the negative test — mint must not auto-rm."""
        assert (minted / "ca.key").exists()

    def test_refuses_overwrite_of_existing_ca(self, minted):
        r = run(["bash", str(MINT), str(minted), PROBE_HOST])
        assert r.returncode != 0
        assert "refusing" in (r.stdout + r.stderr).lower()


class TestLeaves:
    def test_all_static_leaves_minted(self, minted):
        for host in DEFAULT_HOSTS:
            assert (minted / f"leaf.{host}.crt").exists()
            assert (minted / f"leaf.{host}.key").exists()
            assert (minted / f"leaf.{host}.chain.crt").exists()

    def test_leaf_san_and_shape(self, minted):
        for host in DEFAULT_HOSTS:
            text = run(
                ["openssl", "x509", "-in", str(minted / f"leaf.{host}.crt"), "-noout", "-text"]
            ).stdout
            assert f"DNS:{host}" in text
            assert "CA:FALSE" in text
            assert "TLS Web Server Authentication" in text

    def test_leaf_expires_before_ca(self, minted):
        def window(p):
            r = run(["openssl", "x509", "-in", str(p), "-noout", "-startdate", "-enddate"])
            lines = dict(
                l.split("=", 1) for l in r.stdout.strip().splitlines() if "=" in l
            )
            fmt = "%b %d %H:%M:%S %Y %Z"
            nb = datetime.datetime.strptime(lines["notBefore"], fmt)
            na = datetime.datetime.strptime(lines["notAfter"], fmt)
            return nb, na

        ca_nb, ca_na = window(minted / "ca.cert.pem")
        for host in DEFAULT_HOSTS:
            _, leaf_na = window(minted / f"leaf.{host}.crt")
            assert leaf_na <= ca_na

    def test_leaf_key_mode_0600(self, minted):
        for host in DEFAULT_HOSTS:
            mode = oct(os.stat(minted / f"leaf.{host}.key").st_mode & 0o777)
            assert mode == "0o600"

    def test_chain_file_is_leaf_plus_ca(self, minted):
        host = DEFAULT_HOSTS[0]
        leaf = run(["openssl", "x509", "-in", str(minted / f"leaf.{host}.crt")]).stdout
        combined = pathlib.Path(minted / f"leaf.{host}.chain.crt").read_text()
        assert leaf in combined
        assert run(["openssl", "x509", "-in", str(minted / "ca.cert.pem")]).stdout in combined


class TestNameConstraintEnforcement:
    """openssl-side oracle for the NC block (SecTrust oracle runs host-side)."""

    def test_in_scope_leaf_verifies(self, minted):
        r = run(
            ["openssl", "verify", "-CAfile", str(minted / "ca.cert.pem"),
             str(minted / f"leaf.{DEFAULT_HOSTS[0]}.crt")]
        )
        assert r.returncode == 0, r.stdout + r.stderr

    def test_out_of_scope_dns_probe_rejected(self, minted):
        r = run(
            ["openssl", "verify", "-CAfile", str(minted / "ca.cert.pem"),
             str(minted / "probe.crt")]
        )
        assert r.returncode != 0
        combined = r.stdout + r.stderr
        assert "permitted subtree violation" in combined or "excluded subtree" in combined

    def test_foreign_dirname_probe_rejected(self, minted):
        r = run(
            ["openssl", "verify", "-CAfile", str(minted / "ca.cert.pem"),
             str(minted / "probe-dirname.crt")]
        )
        assert r.returncode != 0
        combined = r.stdout + r.stderr
        assert "permitted subtree violation" in combined

    def test_probe_leaves_exist_for_negative_tests(self, minted):
        assert (minted / "probe.crt").exists()
        assert (minted / "probe-dirname.crt").exists()
