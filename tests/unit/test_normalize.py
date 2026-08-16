from reconrelate.core.normalize import normalize_domain


def test_normalize_domain_strips_scheme_and_path() -> None:
    assert normalize_domain("https://Example.COM/path") == "example.com"

