import pytest

from aegis_ai.config import ConfigurationError, Settings


def test_default_settings_use_local_provider():
    settings = Settings()

    assert settings.base_url == "http://127.0.0.1:8000/v1"
    assert settings.model == "local-model"
    assert settings.api_key is None
    assert settings.timeout_seconds == 60.0


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/v1",
        "http://user:password@example.com/v1",
        "https://example.com/v1?api_key=secret",
        "https://example.com:invalid/v1",
    ],
)
def test_settings_reject_unsafe_or_invalid_provider_urls(url):
    with pytest.raises(ConfigurationError):
        Settings(base_url=url)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_settings_reject_invalid_timeouts(timeout):
    with pytest.raises(ConfigurationError):
        Settings(timeout_seconds=timeout)
