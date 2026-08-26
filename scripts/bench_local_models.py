"""Compare local Ollama models on the real relationship-pivot task.

Not a generic benchmark: it drives the exact SYSTEM_PROMPT, evidence projection and
RELATIONSHIP_RESPONSE_FORMAT the tool uses in production, so a pass here means the model can
actually do the job - which published benchmark scores do not tell you. Today's field testing
found models that rank well generally and still ignore the response schema entirely.

Three cases, chosen to separate format compliance from judgment:

  clean-org        a strong registrant org is present  -> should extract it
  registrar-noise  a role mailbox sits next to a real org -> must pick the org, NOT the mailbox
  privacy-redacted everything redacted, generic NS      -> should abstain rather than invent

The middle case is the one that matters. Returning valid JSON is easy; knowing that
registrar-updates@ is shared infrastructure rather than an ownership signal is the actual task.

Usage:
    python scripts/bench_local_models.py                 # every installed Ollama model
    python scripts/bench_local_models.py qwen3.5:9b ...  # only the named ones
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import litellm  # noqa: E402

from reconrelate.llm_orchestration.egress_policy import prepare_model_evidence  # noqa: E402
from reconrelate.llm_orchestration.prompt_builder import (  # noqa: E402
    SYSTEM_PROMPT,
    build_user_message,
)
from reconrelate.llm_orchestration.response_parser import (  # noqa: E402
    RELATIONSHIP_RESPONSE_FORMAT,
    parse_llm_response,
)

OLLAMA = "http://localhost:11434"

CASES = [
    {
        "id": "clean-org",
        "expect": "extract the registrant organization",
        "want_org": "automattic",
        "evidence": {
            "domain": "automattic.com",
            "whois": {
                "registrant_org": "Automattic, Inc.",
                "registrant_name": "Domain Administrator",
                "registrant_email": "admin@automattic.com",
                "registrant_phone": "+1.8772738550",
                "nameservers": ["ns1.automattic.com", "ns2.automattic.com"],
                "creation_date": "2004-06-19", "expiration_date": "2033-06-19",
            },
            "basic_intel": {
                "title": "Automattic - Making the web a better place",
                "description": "We are passionate about making the web a better place.",
                "aliases": ["Automattic"], "copyright_org": "Automattic Inc.",
                "tracker_ids": [], "redirect_domain": "", "legal_entities": ["Automattic, Inc."],
            },
            "subdomains": [],
        },
    },
    {
        "id": "registrar-noise",
        "expect": "pick the org, reject the role mailbox",
        "want_org": "globex",
        "reject_substrings": ["registrar-updates", "abuse@", "markmonitor"],
        "evidence": {
            "domain": "globex.com",
            "whois": {
                "registrant_org": "Globex Corporation",
                "registrant_name": "REDACTED FOR PRIVACY",
                "registrant_email": "registrar-updates@globex.com",
                "registrant_phone": "",
                "nameservers": ["ns1.markmonitor.com", "ns2.markmonitor.com"],
                "creation_date": "1998-03-01", "expiration_date": "2030-03-01",
            },
            "basic_intel": {
                "title": "Globex Corporation", "description": "Global industrial conglomerate.",
                "aliases": ["Globex"], "copyright_org": "Globex Corporation",
                "tracker_ids": [], "redirect_domain": "", "legal_entities": ["Globex Corporation"],
            },
            "subdomains": [],
        },
    },
    {
        "id": "privacy-redacted",
        "expect": "abstain - nothing identifying is present",
        "want_abstain": True,
        "evidence": {
            "domain": "example-privacy.com",
            "whois": {
                "registrant_org": "REDACTED FOR PRIVACY",
                "registrant_name": "REDACTED FOR PRIVACY",
                "registrant_email": "",
                "registrant_phone": "",
                "nameservers": ["ns-1534.awsdns-63.org", "ns-169.awsdns-21.com"],
                "creation_date": "2021-01-01", "expiration_date": "2027-01-01",
            },
            "basic_intel": {
                "title": "", "description": "", "aliases": [], "copyright_org": "",
                "tracker_ids": [], "redirect_domain": "", "legal_entities": [],
            },
            "subdomains": [],
        },
    },
]


def installed_models() -> list[str]:
    with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=10) as r:
        return sorted(m["name"] for m in json.load(r).get("models", []))


async def run_case(model: str, case: dict) -> dict:
    evidence = prepare_model_evidence(case["evidence"], cloud=False)
    user = build_user_message(case["evidence"]["domain"], evidence)
    started = time.perf_counter()
    try:
        response = await asyncio.to_thread(
            litellm.completion,
            model=f"ollama/{model}",
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": user}],
            temperature=0.1, max_tokens=512, api_base=OLLAMA, num_predict=512,
            # Mirrors LLMClient._call_model: Qwen3-family models otherwise spend the whole
            # budget in `thinking` and return empty content.
            think=False,
            response_format=RELATIONSHIP_RESPONSE_FORMAT, timeout=180.0,
        )
    except Exception as exc:
        return {"case": case["id"], "ok": False, "disposition": "error",
                "detail": f"{type(exc).__name__}", "ms": int((time.perf_counter() - started) * 1000)}

    ms = int((time.perf_counter() - started) * 1000)
    parsed = parse_llm_response(response.choices[0].message.content, "relationship")
    values = [f"{p.id_type}:{p.value}" for p in parsed.pivots]
    lowered = " ".join(values).lower()

    if parsed.disposition == "invalid":
        ok, detail = False, "schema violation"
    elif case.get("want_abstain"):
        ok = parsed.disposition == "abstained" or not parsed.pivots
        detail = "abstained" if ok else f"invented {values}"
    else:
        found = case["want_org"] in lowered
        leaked = [b for b in case.get("reject_substrings", []) if b in lowered]
        ok = found and not leaked
        detail = "correct" if ok else (
            f"leaked {leaked}" if leaked else f"missed '{case['want_org']}' (got {values or 'nothing'})"
        )
    return {"case": case["id"], "ok": ok, "disposition": parsed.disposition,
            "detail": detail, "ms": ms, "pivots": len(parsed.pivots)}


async def main() -> None:
    models = sys.argv[1:] or installed_models()
    print(f"Testing {len(models)} model(s) x {len(CASES)} cases against the real pivot schema.\n")
    summary: list[tuple[str, int, int]] = []

    for model in models:
        print(f"=== {model}")
        passed = 0
        total_ms = 0
        for case in CASES:
            result = await run_case(model, case)
            total_ms += result["ms"]
            passed += 1 if result["ok"] else 0
            mark = "PASS" if result["ok"] else "FAIL"
            print(f"  [{mark}] {result['case']:<17} {result['disposition']:<10} "
                  f"{result['ms']:>6} ms  {result['detail']}")
        print(f"  -> {passed}/{len(CASES)} passed, {total_ms/len(CASES):.0f} ms avg\n")
        summary.append((model, passed, total_ms // len(CASES)))

    print("=" * 68)
    print(f"{'MODEL':<42} {'PASS':<8} {'AVG':>10}")
    for model, passed, avg in sorted(summary, key=lambda r: (-r[1], r[2])):
        print(f"{model:<42} {passed}/{len(CASES)}      {avg:>7} ms")


if __name__ == "__main__":
    asyncio.run(main())
