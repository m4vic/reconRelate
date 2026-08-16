# SEC EDGAR completed-acquisition evidence

ReconRelate can read recent Form 8-K Item 2.01 filings from the official SEC EDGAR APIs. The API is
free and needs no key, but the SEC requires automated clients to declare an operator and contact
address.

```powershell
$env:RECONRELATE_SEC_USER_AGENT = "Your Name you@example.com"
reconrelate acquisitions "EXACT SEC FILER TITLE" --source sec-edgar --json
```

Use your real contact information. ReconRelate does not provide a fabricated default identity. The
value is sent only as the `User-Agent` header to `sec.gov` and `data.sec.gov`.

## Evidence boundary

The adapter:

1. Resolves exactly one normalized company title from SEC's company-ticker index.
2. Reads the company's official submissions JSON.
3. Considers at most 25 recent filings and fetches at most five Forms 8-K/8-K-A explicitly tagged
   with Item 2.01.
4. Reads only each filing's primary document, with a one-MiB response ceiling.
5. Emits `acquired` only when completed, closed, or consummated acquisition language is followed by
   a legal-entity name, or when the filing both names a target in an agreement and explicitly
   confirms completion of that same capitalized Acquisition.

Proposals, merger agreements alone, dispositions, vague asset descriptions, fuzzy filer names, and
ambiguous filer matches produce no relationship. Each accepted result retains the filer CIK,
accession number, filing date, official filing URL, and a bounded supporting sentence.

SEC evidence is an organization-to-organization fact. It cannot enqueue a domain unless another
source independently supplies an explicit official organization-to-domain link.

The provider uses a conservative two-requests-per-second internal throttle—below the SEC's published
maximum—as well as ReconRelate's shared request, page, response, concurrency, and timeout limits.
