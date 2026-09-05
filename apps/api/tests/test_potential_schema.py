"""Unit tests for potential Pydantic schemas."""

import uuid

import pytest


class TestPotentialSchemaProvider:
    """Provider field on potential schemas (NFM-296 Task 1)."""

    def test_summary_defaults_to_local_provider(self):
        from nfm_db.schemas.potential import PotentialSummary

        s = PotentialSummary(
            id=uuid.uuid4(),
            name="eam-al",
            type="eam",
        )
        assert s.provider == "local"

    def test_detail_inherits_provider_and_accepts_openkim(self):
        from nfm_db.schemas.potential import PotentialDetail

        d = PotentialDetail(
            id=uuid.uuid4(),
            name="eam-al",
            type="eam",
            provider="openkim",
        )
        assert d.provider == "openkim"

    def test_detail_defaults_to_local(self):
        from nfm_db.schemas.potential import PotentialDetail

        d = PotentialDetail(
            id=uuid.uuid4(),
            name="eam-al",
            type="eam",
        )
        assert d.provider == "local"

    def test_compat_with_existing_extra_field(self):
        """Local DB rows have `source` and `source_doi`; provider must co-exist."""
        from nfm_db.schemas.potential import PotentialDetail

        d = PotentialDetail(
            id=uuid.uuid4(),
            name="eam-al",
            type="eam",
            source="local:upload",
            source_doi="10.1234/foo",
            provider="local",
            extra={"foo": "bar"},
        )
        assert d.source == "local:upload"
        assert d.source_doi == "10.1234/foo"
        assert d.provider == "local"
        assert d.extra == {"foo": "bar"}


class TestPotentialSchemaReferences:
    """``references`` accepts bare-string entries (F3 / NFM-4309 + NFM-4343).

    Three Hunan University potentials in prod (``22d980dc``, ``c6591f31``,
    ``c19b8325``) store ``references`` as a list of bare citation strings
    (e.g. ``"J. Nucl. Mater. 541 (2020) 152421"``) rather than the canonical
    ``[{"doi": ..., "citation": ...}]`` dict list. Today ``PotentialDetail``
    types the field as ``list[dict]`` and Pydantic raises
    ``Input should be a valid dictionary, input_value='...'``, so the
    FastAPI detail endpoint 500s for those rows.

    Fix scope: widen the schema to ``list[dict | str]`` so legacy bare-string
    references survive; the FE render layer already needs to handle the
    string case anyway (NFM-4311 / PR #1184 BFF retarget).
    """

    def test_detail_accepts_bare_string_reference(self):
        from nfm_db.schemas.potential import PotentialDetail

        d = PotentialDetail(
            id=uuid.uuid4(),
            name="EAM_Fe_Hnu_2020",
            type="EAM",
            references=["J. Nucl. Mater. 541 (2020) 152421"],
        )
        assert d.references == ["J. Nucl. Mater. 541 (2020) 152421"]

    def test_detail_accepts_mixed_reference_list(self):
        """Dict + bare-string mix must validate; legacy + new coexist."""
        from nfm_db.schemas.potential import PotentialDetail

        d = PotentialDetail(
            id=uuid.uuid4(),
            name="EAM_Fe_Hnu_2020",
            type="EAM",
            references=[
                {"doi": "10.1234/canonical", "citation": "Canonical ref"},
                "J. Nucl. Mater. 541 (2020) 152421",
                {"citation": "Bare-citation-only dict"},
            ],
        )
        assert len(d.references) == 3
        assert d.references[0]["doi"] == "10.1234/canonical"
        assert d.references[1] == "J. Nucl. Mater. 541 (2020) 152421"
        assert d.references[2]["citation"] == "Bare-citation-only dict"

    def test_detail_defaults_to_empty_list(self):
        from nfm_db.schemas.potential import PotentialDetail

        d = PotentialDetail(
            id=uuid.uuid4(),
            name="eam-no-refs",
            type="EAM",
        )
        assert d.references == []

    def test_create_request_accepts_bare_string_reference(self):
        """The create path must accept bare-string references too — admins
        copy/paste from the same legacy supabase rows."""
        from nfm_db.schemas.potential import PotentialCreateRequest

        req = PotentialCreateRequest(
            name="EAM_Fe_Hnu_2020",
            type="EAM",
            elements=["Fe"],
            system_name="Fe-Hnu",
            description="EAM for Fe (Hunan University)",
            references=["J. Nucl. Mater. 541 (2020) 152421"],
            license_type="open_license",
        )
        assert req.references == ["J. Nucl. Mater. 541 (2020) 152421"]

    def test_detail_rejects_other_scalar_types(self):
        """Numeric / list refs are still rejected — only str + dict allowed."""
        from pydantic import ValidationError

        from nfm_db.schemas.potential import PotentialDetail

        with pytest.raises(ValidationError):
            PotentialDetail(
                id=uuid.uuid4(),
                name="bad-refs",
                type="EAM",
                references=[12345],
            )
