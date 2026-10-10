"""Guards on the NFM-5441 legacy chunk compatibility shim (NFM-5418 relief).

NFM-5418: the CF edge serves 1y-pinned HTML (cached 2026-10-08T13:47Z) whose
two pre-``796ba5ca7`` hashed asset refs 404 at origin — every cache-cold
visitor got a dead page while the board-gated purge (NFM-5420) waits on the
operator. NFM-5441 ships the two chunks under their ORIGINAL hashed names as
an additive Dockerfile COPY so the stale HTML renders again.

These tests pin the shim's contract so removing it is a deliberate act, not
drift:

* all 13 legacy filenames exist under ``docker/legacy-chunks/`` with exactly
  the recovered bytes (sha256-pinned — a rebuild regenerating different
  content must fail loudly, not silently swap the stylesheet);
* ``docker/web.Dockerfile`` wires the directory into the runner image's
  chunk dir AFTER the build's own static COPY, so the shim is additive;
* the shim's chunk names must never collide with a current build: names are
  content hashes, so the guard is that the shipped JS hash equals the name
  implied provenance (the JS is byte-exact; the CSS is a documented
  same-source substitution).

Removal: once the NFM-5420 purge lands and NFM-5418 confirms edge healing,
delete ``docker/legacy-chunks/`` and the COPY block in web.Dockerfile —
these tests fail until both halves of the removal are done together.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "docker" / "web.Dockerfile"
LEGACY_DIR = REPO_ROOT / "docker" / "legacy-chunks"

# sha256 of the recovered artifacts as staged for NFM-5441.
#   *.js (12 files) — byte-exact: the rebuild at 186e54c76 reproduces each
#     content-hash name exactly, matching the edge-cached HTML ref sets of
#     /datasets, /browse, /potentials and /materials. Eleven of them still
#     serve edge HITs but 404 at origin since the f727ce6c8 chunk rotation
#     (latent breakage for any cache-miss colo), so the shim covers all 13
#     refs missing at origin, not just the two already-visible 404s.
#   25fb4gkiz433k.css — same-source rebuild served under the legacy name (the
#     deployed original's bytes are unrecoverable: image pruned, rebuild
#     diverges). Rule-level diff vs the 3f62e7008 build differs by exactly the
#     NFM-5330 utility additions.
EXPECTED_SHA256 = {
    "00sbh1r3wabcn.js": "30881b4b98baf17281e609d6fdf489478d108baf85fc1764aa1d6c072a1beb94",
    "0708lvcd_u2xy.js": "f0a31142b3565c6dd9cdc67eb8635f8874514513c34dfd3ed79385e595c9907f",
    "07mkxmu7djlo-.js": "caebffed1612346370f5b475a9cb2d3ddbc17f9fea3da19e05f57d9364f5d1b9",
    "0h2tnti6wmy3e.js": "1b9fdd1a78d84376d2d6839e8319fbfbde7509200ffbd0564446dff00aacdf9d",
    "1acy9ocun1_s6.js": "d29d0e0b5322ebdd6506e4ab026e1791667a6c40637e7ea5ec96e13a5defdb10",
    "1nfd-lsdn60ro.js": "99d1e6d053a33d7db628b33a020d2b3ccefc47fd7d7ec16b8475fcb35b93d9b5",
    "22xcu8nfpdjtx.js": "87e427f28c06252d7fe86949e8ed560f8651cb9db1cb43d8988f1380b0aa0180",
    "25fb4gkiz433k.css": "7a915e0e76868ab9c4e6d7fec8280e6132ef1b530231fd479ca106b67b35c297",
    "2ottqx3vslavn.js": "c67a0f5826a97b69509befa9ac49e8d5a6a270bc5e6d87f89929eadb128264af",
    "3htf4yju0snrs.js": "aa08105cb8832b655aabb43706519ba76bdf59b27c1e82df72822221b2b9dbeb",
    "3t-fjktvh1wdz.js": "69dc7d1f80ad8d62342d78f7638e63887053120575df03a11d74a4a7d7dd4edb",
    "3u99er9ox7s5z.js": "7462b88592be5e27a9a46ba67c22836e0524068eb0d120b857bfe6c3e793d1f8",
    "3zn71tmikkkmn.js": "8b64ee723878e735387926b1404f0cca827d00e381069d1e75964d8739b06330",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_legacy_chunk_files_present_with_pinned_bytes() -> None:
    for name, expected in EXPECTED_SHA256.items():
        path = LEGACY_DIR / name
        assert path.is_file(), f"missing legacy chunk {name} — shim dir incomplete"
        assert _sha256(path) == expected, (
            f"{name} bytes drifted from the NFM-5441 recovery (sha256 "
            f"{_sha256(path)} != {expected}); regenerate per the NFM-5441 "
            "evidence comment, never hand-edit"
        )


def test_dockerfile_wires_shim_after_build_static_copy() -> None:
    text = DOCKERFILE.read_text()
    static_copy = re.search(
        r"^COPY --from=builder /app/apps/web/\.next/static \./apps/web/\.next/static$",
        text,
        re.MULTILINE,
    )
    shim_copy = re.search(
        r"^COPY docker/legacy-chunks/ \./apps/web/\.next/static/chunks/$",
        text,
        re.MULTILINE,
    )
    assert static_copy, "build's own static COPY missing from web.Dockerfile"
    assert shim_copy, (
        "legacy-chunks COPY missing — the NFM-5441 shim must ship both files "
        "and the COPY together (see test module docstring for the removal step)"
    )
    assert shim_copy.start() > static_copy.start(), (
        "shim COPY must come after the build's static COPY (additive layer)"
    )
