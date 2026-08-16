# Free OSINT APIs and Datasets for Domain Recon and Infra Mapping

## Overview

This report lists high‑quality free or generous‑tier data sources and APIs suitable for building a Python‑based domain reconnaissance and infrastructure mapping tool similar to Amass or SpiderFoot. It focuses on passive DNS, subdomain enumeration, WHOIS/RDAP, web technology tracking, and threat‑intel/reputation correlation.[^1][^2][^3][^4]

For each source, it highlights what data you get, notable limitations, and any registration or rate‑limit details relevant for automation.

## Passive and Historical DNS

### Rapid7 Project Sonar FDNS (Open Data)

Rapid7’s Project Sonar FDNS datasets provide large‑scale forward DNS data (A, AAAA, CNAME, TXT, MX, NS, ANY) for all known forward DNS names. Data is shipped as compressed NDJSON files with name, type, value, and timestamp fields, updated weekly or more frequently.[^5][^6]

Key points for your use case:

- Access model: Free, but you must request Open Data access and log in to download the gzipped datasets.[^6][^5]
- Scale: Multi‑GB files per study (e.g., tens of GB for A/NS/ANY records), so you will want a preprocessing pipeline (e.g., local ClickHouse / SQLite + Python ETL).
- Strengths: Excellent for historical DNS infrastructure mapping, global enumeration of hostnames, and building your own passive DNS‑like index.
- Limitations: Not an online query API; you must handle storage, indexing, and search.

### CIRCL Passive DNS (pdns)

CIRCL operates a passive DNS service with historical records accessible over a JSON REST API following the Passive DNS Common Output Format. Queries are done via `https://www.circl.lu/pdns/query/<queryvalue>` with IPs, hostnames, or domains as query values.[^4][^7]

- Access model: Access is restricted to trusted partners; you must request access and justify your use case and affiliation.[^4]
- Output: JSON lines with `rrname`, `rrtype`, `rdata`, `time_first`, `time_last`, and `count`, which is ideal for programmatic ingestion.[^4]
- Tooling: There is a Python library (PyPDNS) and other client libs that can integrate directly into your recon pipeline.[^4]

If you qualify, CIRCL pDNS gives you near turn‑key passive DNS enrichment for your infra‑graph.

### DNSlytics DNS and Reverse APIs

DNSlytics provides DNS analytics and reverse lookup APIs, including reverse IP/NS/MX and reverse Analytics/AdSense IDs. The free API tier allows up to 2,500 requests per day across tools like ReverseIP, ReverseAdsense, ReverseGAnalytics, and ReverseHistory.[^8]

- Access model: Free API key with daily quota; higher paid tiers provide more credits per day.[^8]
- Useful endpoints:
  - `ReverseIP`: domains hosted on the same IP.[^8]
  - `ReverseAdsense` and `ReverseGAnalytics`: domains using the same Google AdSense or Analytics IDs — powerful for ownership and tracking‑code correlation.[^8]
  - `ReverseHistory`: historical reverse data for Analytics/AdSense, IP, MX, NS for infra evolution.[^8]

For a recon LLM‑graph, DNSlytics is one of the few relatively generous free sources for both DNS and tracking‑ID based pivots.

### DomScan Domain Intelligence API (DNS + Security)

DomScan exposes DNS lookup, reverse NS, IP, health and threat‑related checks in one API with a generous free tier. The docs state that you get 10,000 free credits per month with no credit card required.[^2][^9][^10]

- Access model: Sign up to obtain an API key; 10,000 credits/month free, credits reset on the first of each month.[^9][^10]
- Relevant endpoints: `/v1/dns` for DNS lookups, `/v1/dns/reverse/ns` for reverse NS lookups, and `/v1/health` or `/v1/health/quick` for security/health metadata.[^9]
- Use in recon: Use DNS + reverse NS for mapping hosting and provider relationships, and health/security scores to annotate nodes in your infrastructure graph.

## Subdomain Enumeration

### Project Sonar FDNS + Local Pivoting

As above, Rapid7 FDNS contains A/AAAA/CNAME/TXT/MX/NS records for known forward names. By filtering the dataset for names ending in your target domain (e.g., `*.example.com`), you can build a large, historical subdomain inventory offline.[^5][^6]

- Pros: Essentially unlimited subdomain enumeration constrained only by your compute/storage; no rate limits once you have the data.
- Cons: Requires batch processing, not live on‑demand answers.

### SecurityTrails (Subdomains + DNS History)

SecurityTrails offers an API that exposes subdomains and DNS history for domains. Example usage includes requesting `/v1/domain/example.com/subdomains` with flags like `children_only` and `include_inactive`.[^11]

- Access model: API is primarily paid, but there is a limited free tier suitable for experimentation.[^12][^11]
- Recon use: Quickly enrich your target with subdomains plus historical A/MX/NS movements, then feed that into broader graph building.
- Limits: Public docs highlight this as a paid service, so expect relatively tight free quotas and respect rate limits.

### VirusTotal (Subdomains via Public API)

VirusTotal’s public API exposes domain relationships including subdomains, communicating samples, and resolutions. The public tier is limited to 500 requests per day at a rate of 4 requests per minute.[^13][^14]

- Access model: Free with registration; explicit ToS forbids commercial use and discourages heavy automated use not contributing samples.[^13]
- Recon use: As a supplement to other sources, especially to correlate subdomains seen in malware or malicious infra.
- Rate limits: 4 requests per minute, 500 per day; exceeding this returns HTTP 429 and empty bodies.[^14][^13]

### HackerTarget DNS and Subdomain Tools

HackerTarget provides DNS reconnaissance tools including subdomain enumeration and other DNS lookups, with a small free quota intended for reconnaissance. The free tier is typically around dozens of requests per day (one reference notes about 50 requests/day for security reconnaissance APIs).[^10][^2]

- Access model: Registration‑based API for some tools; basic usage available unauthenticated via web forms.[^2][^10]
- Recon use: Use as one of multiple subdomain sources when your quotas permit, aggregating results along with Sonar and other APIs.

## WHOIS, Reverse WHOIS, and RDAP

### Direct RDAP Queries (Public Registry Endpoints)

There is no truly unlimited managed WHOIS API; even “free” APIs apply rate limits or monthly quotas. The closest to no‑cost bulk access is to directly query RDAP servers listed in the IANA RDAP bootstrap registries.[^15][^16]

- How it works: IANA maintains RDAP bootstrap data mapping TLDs and registrars to base RDAP URLs; you can programmatically follow those to query registration data in JSON format.[^16]
- Limits: Registries and registrars still enforce per‑IP caps (often a few hundred WHOIS/RDAP queries per day), and some do not publish exact limits.[^17][^15]
- Engineering implications: Implement caching, randomized backoff, and spreading queries over time to avoid HTTP 429 responses or silent blocking.[^15][^16]

For Python, you can either hit the RDAP endpoints directly using `requests`, or build on open clients such as `20c/rdap` that understand IANA bootstrap data.[^18]

### Hosted RDAP/WHOIS APIs with Free Tiers

If you prefer not to manage bootstrap logic and per‑TLD quirks yourself, several managed APIs have useful free tiers:

- **WhoisJSON**: Article notes a free tier of roughly 1,000 requests per month with no credit card, plus normalization and rate‑limit handling, advertised as one of the more generous zero‑cost plans.[^19][^15]
- **IP2WHOIS**: Listed in free domain API roundups as offering 500 free WHOIS lookups per month.[^10]

These are best used when you need normalized WHOIS/RDAP data but only for modest volumes.

### Reverse WHOIS Options

Truly free, high‑volume reverse WHOIS APIs are rare due to data licensing and privacy constraints. In practice you can approximate reverse WHOIS in three ways without buying a commercial license:[^20][^15]

- Use DNS and analytics‑ID correlation (via Sonar and DNSlytics) to infer ownership clusters instead of direct reverse WHOIS.
- Apply RDAP pattern searches and registrar metadata where registries expose them (limited and inconsistent).[^16]
- Leverage smaller free tiers of commercial APIs (DomainTools, WhoisXML, etc.) for targeted pivoting, while avoiding dependency on them for bulk discovery.[^20][^10]

## Web Technology and Tracking IDs

### DNSlytics Reverse Analytics and AdSense

As noted earlier, DNSlytics exposes `ReverseGAnalytics` and `ReverseAdsense` APIs that return domains sharing the same Google Analytics or AdSense identifiers. It also provides `ReverseHistory` to see historical relationships between those IDs and domains/IPs.[^8]

- Free tier: 2,500 API requests per day, with each call consuming a number of credits depending on the endpoint (e.g., reverse Analytics and AdSense both consume 6 credits per query).[^8]
- Use in infra graph: Excellent for publisher/owner attribution, tracking multi‑brand portfolios, and discovering shadow infra sharing the same trackers.

### Wappalyzer Technology Detection API

Wappalyzer provides a technology fingerprinting database and API that identifies server software, frameworks, JS libraries, analytics, and more based on HTTP responses and content signatures. A community reference notes that the Wappalyzer API free tier is about 1,000 requests per month, sufficient for light automation.[^21][^22]

- Access model: API key required; free tier around 1,000 requests/month, paid plans for higher volumes.[^22]
- Recon use: Enrich each host in your graph with web stack details (CMS, JS libs, analytics tags) to support tech‑stack correlation and attack‑surface mapping.

### BuiltWith Technology Profiling

BuiltWith provides a web technology lookup service that returns server stack, frameworks, third‑party scripts, and other components from a given URL. The public web UI is free; API access is commercial but can be selectively used in low volume.[^21]

- Recon use: As a fallback to Wappalyzer when you need a second opinion on technology detection or for manual validation.[^21]

### Host.io and Similar Domain Intelligence APIs

Host.io is mentioned in free‑domain‑API roundups as offering technology detection and domain intelligence with a small free tier (around 1,000 requests per month). While not as exhaustive as Wappalyzer, it can quickly give you basic tech signals plus backlinks and DNS metadata.[^10]

- Access model: Free sign‑up with monthly query cap; good candidate for an additional enrichment layer.[^10]

### DNSlytics Reverse Analytics Web Tool

In addition to the API, DNSlytics offers a web‑based Reverse Analytics ID / Google Tag tool showing domains sharing the same Analytics ID or Google Tag, plus historical data via its hosting history tool. The API mirrors this functionality, so you can prototype queries in the UI before integrating into Python.[^23]

## Threat Intelligence and Reputation

### VirusTotal Public API

VirusTotal aggregates scans from 70+ antivirus engines and URL/domain reputation feeds. The public API allows 500 requests/day at 4 requests/min with restrictions on commercial usage.[^3][^14][^13]

- Recon use: Tag domains/IPs in your graph with detection counts, last analysis date, and associated samples, then bias your LLM toward suspicious clusters.
- Constraints: Respect rate limits and ToS; cache results aggressively, and consider only using VT for confirmed IOIs rather than broad sweeps.[^14][^13]

### AbuseIPDB

AbuseIPDB provides crowdsourced reports of abusive IP behavior with an API aimed at system admins and individuals. The free “Individual” plan provides 1,000 IP checks and reports per day and 100 block checks per day, which is generous for a small recon pipeline.[^24][^25]

- Access model: Free forever for individuals; higher tiers for more volume and features.[^25][^24]
- Recon use: For any IP nodes in your graph, annotate with abuse confidence scores and report counts to prioritize suspicious infra.

### GreyNoise Community API

GreyNoise classifies Internet‑wide scan and background noise traffic, distinguishing opportunistic scanners from targeted activity. Community/free accounts can do about 50 searches per week; unauthenticated IP lookups are limited to 10 per day with HTTP 429 for over‑use.[^26]

- Recon use: When you see scanning IPs, check whether they are benign background noise or new/rare scanners.
- Limitations: Low free quotas mean GreyNoise is more for surgical enrichment than wholesale correlation.[^26]

### urlscan.io

urlscan.io captures full page loads (HTML, JS, requests, screenshots) and provides a searchable index and API. Rate limits apply per action (submit, search, etc.) with quotas per minute, hour, and day; limits can be inspected via the `quotas` endpoint and HTTP rate‑limit headers.[^27][^28]

- Recon use: For a domain or URL, you can pull recent scans, extracted domains, requests, and technologies, then feed these into your graph.
- Engineering: Respect fixed‑window limits and use the `X‑Rate‑Limit` headers to pace calls in Python.[^28][^27]

### DomScan Threat and Health Checks

DomScan’s threat‑intelligence endpoints provide security scoring, SSL analysis, and other signals in the same API used for DNS and domain checks. With 10,000 free credits/month, you can reasonably annotate thousands of domains monthly for free.[^3][^9]

- Recon use: Use health/security scores as node attributes, and SSL metadata for certificate‑based pivoting.

### AlienVault OTX (OTX API)

AlienVault’s Open Threat Exchange (OTX) exposes “pulses” of community‑shared indicators via an API with free registration. With an OTX API key, you can retrieve indicators for all pulses you subscribe to via `/api/v1/pulses/subscribed` and other endpoints.[^29][^30]

- Access model: Free account required; obtain API key from the OTX dashboard.[^30]
- Recon use: Ingest domains, URLs, and IPs from relevant pulses to seed or tag nodes in your recon graph.

### Shodan and Censys (Limited but Useful)

- **Shodan**: Free API tier (OSS plan) gives roughly 100 query credits per month, with quotas resetting at the start of each month. Useful for enriching a small number of IPs with open‑port and banner data.[^31][^3]
- **Censys**: Free tier allows about 250 API calls per month, with calls limited to one request every 2.5 seconds; basic host, web property, and certificate data are accessible.[^32][^33]

These are best treated as high‑value enrichers for a subset of high‑priority nodes rather than core data sources.

## Putting It Together in a Python Workflow

### Design Principles

Given the quotas and constraints above, an effective Python recon engine should:

- Use bulk datasets (Rapid7 Sonar FDNS) as the primary source for passive DNS/subdomains and perform local indexing.
- Layer on mid‑volume free APIs with generous tiers (DomScan, DNSlytics, AbuseIPDB, urlscan.io, Wappalyzer) for enrichment.
- Reserve low‑quota, high‑value sources (VirusTotal, GreyNoise, Shodan, Censys, CIRCL pDNS) for targeted pivoting.
- Implement caching, asynchronous batching, and backoff tuned to documented rate limits.

### Example Integration Patterns

Some practical patterns for your infra‑graph builder:

- **Subdomain and DNS graph core:**
  - Pre‑process Rapid7 FDNS into a local store keyed by fqdn, domain, and IP.[^6]
  - Enrich select domains via DomScan `/v1/dns` and DNSlytics ReverseIP/ReverseNS for additional relationships.[^2][^9][^8]

- **Ownership and tracker clustering:**
  - Use DNSlytics `ReverseGAnalytics`/`ReverseAdsense` plus ReverseHistory to group domains by GA/AdSense IDs and historical NS/MX/IP usage.[^8]
  - Supplement with Wappalyzer API detections to correlate technology fingerprints.[^22][^21]

- **Registration layer:**
  - Implement a small RDAP client using IANA bootstrap to fetch `registrar`, `contact`, and `status` for a limited set of domains, with Redis or on‑disk caching and exponential backoff for 429s.[^15][^16]
  - Optionally, integrate WhoIsJSON/IP2WHOIS for normalized data within free quotas.[^15][^10]

- **Threat and reputation edge weighting:**
  - For suspicious or high‑centrality nodes, query VirusTotal, AbuseIPDB, GreyNoise, and urlscan.io.[^24][^28][^13][^26]
  - Feed detection counts, abuse scores, and scan artifacts as edge weights or node labels in your LLM‑driven graph.

- **LLM‑ready graph representation:**
  - Normalize all API responses to a common schema (entity types: domain, host, IP, trackingID, certificate; edge types: resolves_to, hosted_on, shares_tracker, shares_NS, related_in_threat_feed, etc.).
  - Serialize into a graph database (e.g., Neo4j) or compact JSON that your LLM layer can ingest.

## Summary of Notable Free/Geneaous Sources

| Category | Source | Free Quota / Access | Standout Use |
|---------|--------|---------------------|--------------|
| Passive DNS & DNS | Rapid7 Sonar FDNS | Free bulk datasets, login required | Historical DNS and large‑scale subdomain discovery[^6] |
| Passive DNS & DNS | CIRCL pDNS | Free for approved partners | Live historical DNS enrichment[^4][^7] |
| DNS & Reverse | DNSlytics API | 2,500 API requests/day | Reverse IP, NS, MX, Analytics, AdSense, historical pivots[^8] |
| DNS & Threat | DomScan API | 10,000 credits/month | DNS, reverse NS, health and threat info in one API[^9][^10] |
| Subdomains & Threat | SecurityTrails | Limited free tier | Subdomains + DNS history for domains[^11][^12] |
| Subdomains & Threat | VirusTotal | 500 req/day, 4 rpm | Domain/subdomain relations + AV verdicts[^13][^14] |
| WHOIS/RDAP | Direct RDAP | Public endpoints, per‑registry limits | Low‑level registration data at protocol level[^16][^15] |
| WHOIS API | WhoisJSON | ~1,000 req/month free | Managed WHOIS/RDAP with generous free tier[^15][^19] |
| Web Tech | Wappalyzer API | ~1,000 req/month free | Technology fingerprinting from HTTP responses[^21][^22] |
| Web Tech & DNS | Host.io | ~1,000 req/month free | Basic tech detection + domain intel[^10] |
| Tracker Correlation | DNSlytics Reverse Analytics/AdSense | Within 2,500 req/day limit | Cluster domains by Google Analytics/AdSense IDs[^8][^23] |
| Threat Intel | VirusTotal | 500 req/day, 4 rpm | AV‑backed domain/IP reputation[^13][^14] |
| Threat Intel | AbuseIPDB | 1,000 IP checks/day free | Crowdsourced abusive IP reports and scores[^24][^25] |
| Threat Intel | GreyNoise | ~50 searches/week | Distinguish Internet background noise from novel scanners[^26] |
| Threat Intel | AlienVault OTX | Free with account | Community threat pulses and indicators[^29][^30] |
| Threat Intel | Shodan | ~100 query credits/month free | Service/port‑level enrichment[^31] |
| Threat Intel | Censys | 250 calls/month free | Host and certificate‑level Internet scan data[^32][^33] |
| Web Scans | urlscan.io | Per‑min/hour/day rate limits, free | Full‑page scan artifacts and extracted IOCs[^27][^28] |

This mix gives you a strong, mostly free foundation for an Amass/SpiderFoot‑like recon tool that builds rich relationship graphs enriched with DNS, registration, technology, and threat‑intel signals.

---

## References

1. [The complete subdomain Enumeration Guide](https://hacktify.in/the-complete-subdomain-enumeration-guide/)

2. [Best DNS Lookup APIs (2026) - DomScan](https://domscan.net/best/dns-lookup-api) - Compare the best DNS lookup APIs for 2025. Query A, AAAA, MX, TXT, NS records and more programmatica...

3. [Best Domain Threat Intelligence APIs (2025) - DomScan](https://domscan.net/best/domain-threat-intelligence-api) - Compare the best domain threat intelligence APIs for 2025. Detect malware, phishing, and malicious d...

4. [Passive DNS](https://old.securitymadein.lu/tools/passive-dns/) - SECURITYMADEIN.LU is the main online source for cybersecurity in Luxembourg. Its goal is to provide ...

5. [Forward DNS (FDNS) | Rapid7 Open Data](https://opendata.rapid7.com/sonar.fdns_v2/?page=87) - Dataset Details. This dataset contains the responses to DNS requests for all forward DNS names known...

6. [Forward DNS (FDNS) - Rapid7 Open Data](https://opendata.rapid7.com/sonar.fdns_v2/) - DNS 'ANY', 'A', 'AAAA', 'TXT', 'MX', and 'CNAME' responses for known forward DNS names

7. [Passive DNS - CIRCL.lu](https://www.circl.lu/services/passive-dns/) - CIRCL Passive DNS (v2) is a database of historical DNS records.

8. [API Access to DNSlytics](https://dnslytics.com/api/) - Our free API access is limited to 2,500 API requests per day. The Premium API is by default limited ...

9. [API Documentation - DomScan Domain Intelligence API](https://domscan.net/docs) - Start for Free: DomScan includes 10,000 free credits per month. No credit card required. We'll give ...

10. [Best Free Domain APIs (2026) - DomScan](https://domscan.net/best/free-domain-api) - Best Free Domain APIs - Top 10 ; DomScan · Modern Domain Intelligence API. 10,000 free credits/month...

11. [Cyber Security API, Threat Intelligence API, Domain, DNS and IP ...](https://securitytrails.com/corp/api) - Passive DNS. Get monthly access to over 1 billion passive DNS datasets. Instant IP & Domain search. ...

12. [SecurityTrails “Your Secret Weapon for Effective Threat Intelligence”](https://www.infopercept.com/blogs/securitytrails-your-secret-weapon-for-effective-threat-intelligence) - With SecurityTrails, you can conduct passive DNS analysis to identify patterns and anomalies in DNS ...

13. [Public vs Premium API - VirusTotal documentationdocs.virustotal.com › reference › public-vs-premium-api](https://docs.virustotal.com/reference/public-vs-premium-api) - While many of the endpoints and features provided by the VirusTotal API are freely accessible to all...

14. [Getting Started with VirusTotal API: No-Code Automation Guide - Tines](https://www.tines.com/blog/virustotal-api-security-automation/) - Explore the VirusTotal API with this guide. Learn how to integrate no-code automation, obtain API ke...

15. [Free WHOIS API — Best Options & Free Plans in 2026 - WhoisJSON](https://whoisjson.com/blog/free-whois-api) - Free tiers often impose strict rate limits (e.g. 5–20 requests per minute). ... Is there a completel...

16. [Requirements for RDAP Servers providing Domain Name ...](https://www.iana.org/help/rdap-requirements)

17. [Free services to fetch WHOIS data? : r/Domains - Reddit](https://www.reddit.com/r/Domains/comments/1j1eani/free_services_to_fetch_whois_data/) - If you need an API, WhoisXML API or RDAP are solid choices. If you only need occasional lookups, com...

18. [20c/rdap: python rdap client · GitHub](https://github.com/20c/rdap) - usage: rdap [-h] [--debug] [--home HOME] [--verbose] [--quiet] [--version] [--output-format OUTPUT_F...

19. [Bulk WHOIS Lookup: How to Query Multiple Domains at Once with ...](https://whoisjson.com/blog/bulk-whois-api-multiple-domains) - The solution is controlled parallelisation: sending multiple requests concurrently within the rate l...

20. [Service Limits and Quotas - DomainTools Technical Documentation](https://docs.domaintools.com/api/dnsdb/reference/rate-limits/) - Service Limits and Quotas¶. The DNSDB API implements quota management to control usage and ensure fa...

21. [Fingerprinting Web Application Technologies - Predatech](https://predatech.co.uk/fingerprinting-web-application-technologies/) - Wappalyzer is a free browser plugin that fingerprints all types of application components including ...

22. [I built a workflow that scans any website and tells me exactly what tech they're using just saved my dev team 20+ hours per week](https://www.reddit.com/r/n8n/comments/1lto8jj/i_built_a_workflow_that_scans_any_website_and/) - I built a workflow that scans any website and tells me exactly what tech they're using just saved my...

23. [Reverse Analytics ID / Google Tag - Find domains sharing the same ...](https://dnslytics.com/reverse-analytics/) - This tool finds domains sharing the same Analytics ID or Google tag. You can search by domain or Goo...

24. [API Plans & Pricing - AbuseIPDB](https://www.abuseipdb.com/pricing) - Individual. FREE. Forever! No Credit Card Required. For individuals or system admins wanting to try ...

25. [Frequently Asked Questions - AbuseIPDB](https://www.abuseipdb.com/faq.html) - Regular users get 1,000 API requests per day. Verified webmasters get 3,000 API requests per day. Yo...

26. [Using the GreyNoise Community API](https://docs.greynoise.io/docs/using-the-greynoise-community-api) - HTTP code 429 - Daily Rate-Limit Exceeded. JSON. { "plan": "unauthenticated", "rate-limit": "10 IP l...

27. [API Rate Limits - urlscan Pro](https://docs.urlscan.io/pages/api-rate-limits) - Most actions on urlscan.io are subject to quotas and rate limits, regardless of whether they are per...

28. [API Documentation - urlscan.io](https://urlscan.io/docs/api/) - If you exceed a rate-limit for an action, the API will respond with a HTTP 429 error code for additi...

29. [Incoming feed - AlienVault OTX Pulses Feed](https://docs.eclecticiq.com/extensions/current/integrations/alienvault/incoming-alienvault-otx-pulse/) - This article describes how to configure incoming feeds for a particular feed source. To see how to c...

30. [Alienvault OTX Integration - Elastic](https://www.elastic.co/docs/reference/integrations/ti_otx) - This integration is for Alienvault OTX. It retrieves indicators for all pulses subscribed to a speci...

31. [How much searches can we do in shodan and after what time the ...](https://stackoverflow.com/questions/63050635/how-much-searches-can-we-do-in-shodan-and-after-what-time-the-api-recharges) - Shodan API usage limits reset at the start of every month. And you're on the Free API tier ( oss pla...

32. [Feature and Data Access Tiers - Censys Documentation](https://docs.censys.com/docs/data-access-tiers-entitlements) - Censys Free users are limited to 1 page of 100 results. Censys Starter. If you purchase credits thro...

33. [Censys - Axonius Documentation](https://docs.axonius.com/docs/censys) - In addition, there is a 25,000 call-per-month limit for the basic paid version, and a 250 call-per-m...

