import pytest

from reconrelate.core.errors import SecurityError
from reconrelate.security.safe_target import validate_scan_target


def test_blocks_loopback_ipv4() -> None:
    with pytest.raises(SecurityError):
        validate_scan_target("127.0.0.1")


def test_blocks_localhost_hostname() -> None:
    with pytest.raises(SecurityError):
        validate_scan_target("localhost")


def test_blocks_metadata_ip_hostname() -> None:
    with pytest.raises(SecurityError):
        validate_scan_target("169.254.169.254")


def test_blocks_private_ipv4() -> None:
    with pytest.raises(SecurityError):
        validate_scan_target("10.0.0.1")


def test_blocks_dot_local() -> None:
    with pytest.raises(SecurityError):
        validate_scan_target("host.internal.lan")


def test_allows_public_domain() -> None:
    validate_scan_target("example.com")


def test_allows_public_ipv4_not_used_as_domain_typically() -> None:
    # 1.1.1.1 is public Cloudflare DNS — literal IP as "host" still allowed for scanning
    validate_scan_target("1.1.1.1")
