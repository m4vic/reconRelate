from reconrelate.core.normalize import normalize_domain


def test_normalize_domain_strips_scheme_and_path() -> None:
    assert normalize_domain("https://Example.COM/path") == "example.com"



# --- role-mailbox filtering (regression: registrar-updates@salesforce.com -> gov.in) --------

def test_role_mailbox_on_own_domain_is_rejected() -> None:
    from reconrelate.llm_orchestration.response_parser import is_registrar_email, is_role_mailbox

    # Sits on the company's OWN domain, so the registrar-domain list cannot catch it.
    assert is_role_mailbox("registrar-updates@salesforce.com")
    assert is_registrar_email("registrar-updates@salesforce.com")
    # Variants the enumerated list would have missed.
    assert is_role_mailbox("registrar-notices@example.com")
    assert is_role_mailbox("domainadmin@example.com")
    assert is_role_mailbox("dnsadmin@example.com")


def test_genuine_owner_contacts_still_pass() -> None:
    from reconrelate.llm_orchestration.response_parser import is_registrar_email, is_role_mailbox

    for good in ("admin@acme.com", "it@acme.com", "security@acme.com", "hostmaster2@acme.com"):
        assert not is_role_mailbox(good), good
        assert not is_registrar_email(good), good


def test_validate_pivot_rejects_role_mailbox_even_from_the_model() -> None:
    from reconrelate.core.types import PivotCandidate
    from reconrelate.llm_orchestration.response_parser import validate_pivot

    bad = PivotCandidate("email", "registrar-updates@salesforce.com", 0.9, "LLM said so")
    assert validate_pivot(bad) is False
    good = PivotCandidate("email", "admin@salesforce.com", 0.9, "owner contact")
    assert validate_pivot(good) is True
