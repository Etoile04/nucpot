"""
Simple smoke test to verify package installation
"""


def test_package_import():
    """Test that package can be imported"""
    from nfm_md_runner import __version__
    assert __version__ == "0.1.0"


def test_config_creation():
    """Test that Settings can be instantiated"""
    from nfm_md_runner.config import Settings

    settings = Settings()
    assert settings.app_name == "nfm-md-runner"
    assert settings.app_version == "0.1.0"
