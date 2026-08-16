from reconrelate.core.normalize import registrable_domain


def test_plain_two_label_domain_is_itself() -> None:
    assert registrable_domain("example.com") == "example.com"


def test_subdomain_collapses_to_apex() -> None:
    assert registrable_domain("blog.example.com") == "example.com"
    assert registrable_domain("a.b.c.example.com") == "example.com"


def test_multi_label_suffix_kept() -> None:
    # foo.co.uk must collapse to foo.co.uk, not co.uk.
    assert registrable_domain("shop.foo.co.uk") == "foo.co.uk"
    assert registrable_domain("foo.co.uk") == "foo.co.uk"


def test_case_and_trailing_dot_normalized() -> None:
    assert registrable_domain("Blog.Example.COM.") == "example.com"


def test_seed_subdomains_share_seed_apex() -> None:
    # The scope fix relies on this: every subdomain of the scanned domain maps to the
    # same registrable domain, so it gets dropped instead of counted as a new domain.
    seed = registrable_domain("automattic.com")
    for host in ("blog.automattic.com", "api.automattic.com", "a.b.automattic.com"):
        assert registrable_domain(host) == seed
