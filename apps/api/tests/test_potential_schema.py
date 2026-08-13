"""Unit tests for potential Pydantic schemas."""

import uuid


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
