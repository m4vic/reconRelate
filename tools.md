# tools.md — Verified Pricing & Sourcing for a Solo OSINT/Recon Hunter
_Last verified: 18 July 2026. All prices pulled by opening each vendor's own pricing page. Every price cell cites the exact vendor URL. Prices change often — reverify before committing spend._

## TL;DR
- **Reverse-WHOIS on a solo budget:** Whoxy is the clear best value — $10 per 1,000 lookups ($0.01/lookup) with pay-as-you-go and no monthly fee (whoxy.com/pricing.php). WhoisFreaks (500 free credits, then $150 one-time for 50,000 lifetime credits) is the best free-to-start alternative (whoisfreaks.com/pricing/api-plans). SecurityTrails is enterprise-priced ($500/mo floor, securitytrails.com/corp/pricing) and not worth it solo.
- **LLM tiering:** Run high-volume classification on Groq Llama 3.1 8B ($0.05/$0.08 per 1M) or DeepSeek V4 Flash ($0.14/$0.28, cache hits $0.0028); escalate hard judgments to Gemini 3.1 Pro ($2/$12) or DeepSeek V4 Pro ($0.435/$0.87). A realistic solo month (10M cheap + 500K frontier tokens) costs roughly $1–$3.
- **OSS recon stack is still 100% free/open-source:** subfinder, httpx, katana, nuclei, interactsh (all MIT, ProjectDiscovery) and OWASP Amass (Apache-2.0). ProjectDiscovery's paid Cloud Platform is optional; the CLIs remain free.
- **Attack/scan tooling (Section 4):** OSS attack tools stay free (sqlmap, ffuf, wfuzz, dalfox, nuclei, interactsh). Worth paying for on the attack side: Burp Suite Pro ($499/yr) and Shodan Membership ($49 one-time); scan engines (Netlas, FOFA, ZoomEye, Censys) have usable free tiers before a ~$49/mo upgrade.

---

## Section 1 — Reverse-WHOIS / Domain-Intel APIs

**Use case:** "given an org name/email, find all its domains." Read the GDPR caveat at the end of this section before trusting recall.

| Provider | Reverse-WHOIS price | What 1 unit buys | Free tier | Rate limits | Fine print | Verified? |
|---|---|---|---|---|---|---|
| **Whoxy** | $10 / 1,000 queries ($0.01 each); $2 / 200; $100 / 10,000; down to $2/1,000 at 5M volume | 1 query = 1 result page. Default 100 results/page (page 2, 3… each cost 1 more query). `mode=mini` returns up to 1,000 results/call; `mode=micro` up to 2,500 (no contact info); `mode=domains` up to 50,000 (keyword search only) | Free API credits via Free Whois API Program (students/non-profits, up to 250,000) | Not published on pricing page | **No charge if search returns zero results.** Each additional result page = 1 more credit. Pay-as-you-go, zero monthly fee. Database = 705,597,057 domains per whoxy.com/whois-database | Verified — vendor page (whoxy.com/pricing.php, footer "2012-2026") |
| **WhoisFreaks** | 50,000 credits for $150 one-time (lifetime, non-expiring); or $75/mo; or $63/mo billed yearly. Surcharge $0.0025–$0.0030/extra credit | 1 credit per reverse/historical request (paged) | 500 free credits on signup, no card | Reverse/Historical: 10 rpm on 50k plan (1 rpm on free tier); Live 80 rpm; Bulk 20 rpm | No credits charged on 4xx errors. 4.1B+ WHOIS records, back to 1986. Single credit pool across all APIs | Verified — vendor page (whoisfreaks.com/pricing/api-plans, ©2026) |
| **ViewDNS.info** | Reverse-WHOIS included in general query quota: prepaid 1,000 queries $49 one-time; monthly 1,000/$29, 2,500/$49 (Developer $49/mo), 15,000/$149, 100,000/$549 | 1 query = 1 reverse-WHOIS call; **default returns first 1,000 results** | Sandbox: 250 free trial queries | Not published as rps; quota-based | One quota shared across all ViewDNS APIs (reverse IP/MX/NS/WHOIS, etc.). Higher per-query cost than Whoxy | Verified — vendor page (viewdns.info/api/pricing/, ©2026) |
| **WhoisXML API (Reverse WHOIS)** | 1 DRS credit per request. **Dollar price per credit NOT verifiable** — pricing tables are JavaScript-rendered / behind login | 1 credit = 1 request (returns a page of results) | 500 free DRS credits, no card | Per-minute limits by annual tier (not shown without login) | Reverse WHOIS = 1 credit; WHOIS History API = 50 credits/request; Subdomains Lookup = 10 credits. Credits non-refundable, may expire | Partially verified — credit cost & free tier verified (reverse-whois.whoisxmlapi.com/api); **dollar price unverified** |
| **SecurityTrails (Recorded Future)** | Professional $500/mo (20,000 queries); Business $1,500/mo (65,000); Enterprise contact sales; OSINT Toolkit self-serve (2,500 queries/mo, price not shown) | 1 query = 1 API call | Historic 50 queries/mo free tier **no longer shown** on vendor pricing page (only attested by third parties) | By plan; not published in detail | Strong for historical DNS & reverse pivots; overpriced for solo. Self-serve "Buy Now" checkout still exists for the two paid tiers | Verified — vendor page (securitytrails.com/corp/pricing) for paid tiers; free tier Unverified |
| **Netlas.io** | Community free; paid self-serve starts ~$49/mo (exact tiers JS-rendered) | 1 "Search coin" = 1 document retrieved | Community: forever-free, 50 requests/day, non-commercial | By plan; download/result caps on lower tiers | Has Domain WHOIS + IP WHOIS search collections; CVE/tag filters need Business tier. Armenia-based (2022) | Partially verified — free tier & model verified (netlas.io/pricing, docs.netlas.io); exact paid tier dollar figures Unverified (JS-rendered) |

**Free/near-free options worth keeping in the kit (not reverse-WHOIS per se, but for the same "map an org" goal):**
- **crt.sh** — free certificate-transparency search (find subdomains/related certs). No cost.
- **Chaos by ProjectDiscovery** — free public bug-bounty subdomain dataset.
- **DomainTools / RiskIQ (PassiveTotal)** — deliberately skipped: enterprise-priced, no realistic solo self-serve tier.

### Correction notes (Section 1)
- **"Whoxy charges per domain returned" — WRONG.** Whoxy charges **per result page (1 query)**, not per domain. A single query in `mini` mode returns up to 1,000 domains for one credit ($0.01). Returning N domains does **not** cost N credits.
- **"SecurityTrails still has a generous free self-serve tier" — OUTDATED.** The historic 50-queries/month free tier is no longer displayed on the vendor's own pricing page as of July 2026; self-serve now starts at $500/mo (Professional).
- **"WhoisXML is cheap per lookup" — UNVERIFIABLE claim.** The per-credit dollar cost is not exposed on any vendor page without login; do not quote a number you cannot see.

### The GDPR recall caveat (read this before buying anything for reverse-WHOIS)
Reverse-WHOIS recall for the "given an org, find its domains" task has **collapsed since GDPR/ICANN Temp Spec (May 2018)**. The scale of the redaction is well documented:
- Per **Interisle Consulting Group's 2024 report** (published via DNIB.com, ICANN's Domain Name Industry Brief): "The other 86.4% of domain records had redacted contact data or were privacy/proxy-protected. In early 2024, we observed that only 10.8% of domain records identified the actual registrant" — down from **75.7% identifiable in early 2018, pre-GDPR.**
- A **WhoisXML API** study of 285,238,124 gTLD domains found "only 77,918,723 domains (27.32%) have registrant email addresses. The rest — exactly 207,319,401 (72.68%) — did not include any email addresses."

Consequences:
- Reverse-WHOIS by **registrant name/org/email** mostly surfaces **pre-2018 registrations** and the ~11% of records where the owner deliberately left WHOIS public. It will **miss the ~86% of modern privacy-protected domains.**
- The providers above all draw from largely the **same underlying WHOIS/RDAP data**, so paying more rarely buys materially better recall for redacted domains.
- **Best practical recall for a solo hunter comes from combining pivots**, not from one reverse-WHOIS product: reverse-WHOIS (historical, pre-2018) + certificate transparency (crt.sh) + shared-nameserver/reverse-NS + reverse-IP/ASN + favicon hashes + the free OSS subdomain stack in Section 3.

**Judgment:** For a self-funded solo hunter, **Whoxy gives the best recall-per-dollar** — cheapest per query, pay-as-you-go, no charge on empty results, and its `mini`/`micro` modes return up to 1,000–2,500 domains for a single $0.01 credit, which is unbeatable for the historical (pre-2018) footprint that reverse-WHOIS can actually still see. Start with **WhoisFreaks' 500 free credits** to test recall on your targets before spending anything. Do not expect any paid product to recover the ~86% of post-2018 records that are redacted at the registry level — spend the money on pivots (CT logs, reverse-IP/NS) instead.

---

## Section 2 — LLM API Cost + Latency (tiered: cheap volume model + frontier judge)

All token prices are USD per 1 million tokens (input / output) unless noted.

| Provider / Model | Input / Output (per 1M) | Prompt caching | Batch | Free tier | Latency | Verified? |
|---|---|---|---|---|---|---|
| **Anthropic Claude Haiku 4.5** | $1.00 / $5.00 | Cache read ~10% of input (~$0.10); cache write 1.25× (5-min) / 2× (1-hr) | −50% ($0.50/$2.50) | No standing free API tier | Fastest/cheapest Claude; 200K context | Verified — platform.claude.com/docs/en/about-claude/pricing |
| **Anthropic Claude Sonnet 5** | $2.00 / $10.00 intro **through Aug 31, 2026**, then $3.00 / $15.00 | Cache read ~10%; write 1.25×/2× | −50% | None | 1M context, up to 128K output | Verified — platform.claude.com pricing / claude.com/pricing |
| **Anthropic Claude Opus 4.8** | $5.00 / $25.00 | Cache read $0.50 (90% off); write 1.25×/2× | −50% ($2.50/$12.50) | None | 1M context, no long-context surcharge; Fast Mode $10/$50 | Verified — platform.claude.com pricing |
| **Google Gemini 2.5 Flash-Lite** | $0.10 / $0.40 (**retires 16 Oct 2026**) | Cache read ~10%; storage $1/M/hr | −50% | Yes (AI Studio, rate-limited) | 1M context, flat regardless of prompt length | Verified — ai.google.dev/gemini-api/docs/pricing |
| **Google Gemini 3.1 Flash-Lite** | $0.25 / $1.50 | Cache read ~10% | −50% | Yes (Flash tiers only) | 1M context, flat | Verified — ai.google.dev pricing |
| **Google Gemini 3.5 Flash** | $1.50 / $9.00 | Cache read ~10% ($0.15) | −50% | Flash free tier | 1M context, flat; native Search grounding | Verified — ai.google.dev pricing |
| **Google Gemini 3.1 Pro** | $2.00 / $12.00 (≤200K); **$4.00 / $18.00 above 200K** | Cache read $0.20 (90% off) | −50% ($1.00/$6.00) | Paid-only (no free tier since Apr 1, 2026) | 1M context; thinking tokens billed as output | Verified — ai.google.dev pricing |
| **DeepSeek V4 Flash** | $0.14 / $0.28 (cache-miss); **cache-hit $0.0028** input | Automatic disk cache; hit = $0.0028 (~98% off) | Not offered as flat batch discount | 5M free tokens on new account | 1M context, 384K max output; China-hosted (variable latency; 503s at peak) | Verified — api-docs.deepseek.com/quick_start/pricing |
| **DeepSeek V4 Pro** | $0.435 / $0.87 (cache-hit input ~$0.003625) | Automatic cache | — | Uses same account credits | 1M context, 384K output | Verified — api-docs.deepseek.com/quick_start/pricing |
| **Groq Llama 3.1 8B Instant** | $0.05 / $0.08 | — | −50% (batch) | Yes (30 rpm / 1,000 rpd, no card) | ~840 tokens/sec (vendor page) | Verified — groq.com/pricing |
| **Groq GPT-OSS 20B** | $0.075 / $0.30 (cache $0.0375) | Cache read 50% | −50% | Free tier | ~1,000 tokens/sec (vendor page) | Verified — groq.com/pricing |
| **Groq GPT-OSS 120B** | $0.15 / $0.60 (cache $0.075) | Cache read 50% | −50% | Free tier | ~500 tokens/sec (vendor page) | Verified — groq.com/pricing |
| **Groq Llama 3.3 70B Versatile** | $0.59 / $0.79 | — | −50% | Free tier | ~394 tokens/sec (vendor page) | Verified — groq.com/pricing |
| **Groq Kimi K2** | $1.00 / $3.00 (cache $0.50) | Cache read 50% | −50% | Free tier | Groq LPU speed; strongest reasoning on Groq | Verified — groq.com/pricing |

### Latency notes
- Groq publishes tokens/sec on its own pricing page (e.g., Llama 3.1 8B at ~840 TPS, GPT-OSS 20B at ~1,000 TPS, Llama 3.3 70B at ~394 TPS). These are vendor-stated throughput figures.
- **Third-party benchmark (Artificial Analysis, live 72-hr data at artificialanalysis.ai/providers/groq — not vendor-verified):** the fastest Groq model measured is **GPT-OSS 20B (high) at 934.4 tokens/sec**; Llama 3.1 8B at **653 t/s**; the cheapest blended is Llama 3.1 8B at $0.05/1M. Note Artificial Analysis independently benchmarked **Llama 3.3 70B at 276 tokens/sec** (Groq's own blog cites this as "the fastest of all benchmarked providers"), slightly below the ~394 TPS Groq lists on its pricing page.
- **Time-to-first-token:** Groq's marketing claim of "<100ms" TTFT is a **vendor figure, not the independently measured number.** Artificial Analysis measures Groq TTFT materially higher — **0.60–0.87s across its catalog** (per infrabase.ai, July 2026), with the lowest being GPT-OSS 120B (low) at ~0.75s. Treat the sub-100ms claim skeptically.
- Anthropic and Google do not publish per-model TPS/TTFT on their pricing pages; latency for those must be taken from third-party benchmarks.

### Correction notes (Section 2)
- **"Claude Sonnet is $3/$15" — partially outdated:** Sonnet 5 is on **introductory $2/$10 pricing through Aug 31, 2026**; the $3/$15 standard rate resumes Sept 1, 2026.
- **"Gemini Pro is a flat $2/$12" — WRONG above 200K tokens:** Gemini 3.1 Pro **doubles to $4/$18** once a request exceeds 200K input tokens. Flash/Flash-Lite tiers stay flat.
- **"DeepSeek off-peak discount (16:30–00:30 UTC, 50–75% off)" — was true for V3/R1 but NOT confirmed for V4.** Do not assume it applies to V4 Flash/Pro; verify on the pricing page before relying on it.
- **"deepseek-chat / deepseek-reasoner" aliases retire 24 July 2026** — migrate to `deepseek-v4-flash` / `deepseek-v4-pro`.
- **Gemini 2.5 Flash-Lite retires 16 Oct 2026**; 2.0 Flash/Flash-Lite already shut down 1 June 2026.

### Cost math for a solo user (10M cheap-tier + 500K frontier-tier per month)
Assume classification is input-heavy (say 8M input / 2M output) and frontier judgment is output-heavier (350K input / 150K output).

**Volume tier (10M tokens/month):**
- Groq Llama 3.1 8B: 8×$0.05 + 2×$0.08 = **$0.56/mo**
- DeepSeek V4 Flash (cache-miss): 8×$0.14 + 2×$0.28 = **$1.68/mo** (far less with caching — cache hits at $0.0028 input)
- Gemini 2.5 Flash-Lite: 8×$0.10 + 2×$0.40 = **$1.60/mo**
- Claude Haiku 4.5 (for comparison): 8×$1 + 2×$5 = **$18.00/mo**

**Frontier tier (500K tokens/month):**
- DeepSeek V4 Pro: 0.35×$0.435 + 0.15×$0.87 = **$0.28/mo**
- Gemini 3.1 Pro: 0.35×$2 + 0.15×$12 = **$2.50/mo**
- Claude Sonnet 5 (intro): 0.35×$2 + 0.15×$10 = **$2.20/mo**
- Claude Opus 4.8: 0.35×$5 + 0.15×$25 = **$5.50/mo**

**Recommendation:** cheapest viable volume model = **Groq Llama 3.1 8B** (speed + $0.56/mo) or **DeepSeek V4 Flash** if you want a bigger model and heavy caching. Best "hard-judgment" model on a budget = **Gemini 3.1 Pro** (best quality/$ with a usable free tier for testing) or **DeepSeek V4 Pro** (near-flagship reasoning for cents). Total realistic monthly spend: **~$1–$3/month**, i.e., a rounding error. All three free tiers (Groq 30 rpm/1,000 rpd; Gemini AI Studio; DeepSeek 5M free tokens) let you prototype at $0.

---

## Section 3 — Free OSS Recon Stack (optional, lower priority)

All confirmed free and open-source as of July 2026.

| Tool | Project | License | Install one-liner | API keys needed? | Verified? |
|---|---|---|---|---|---|
| **subfinder** | ProjectDiscovery | MIT | `go install -v github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest` | Optional free API keys for some sources (more sources = better recall) | Verified — github.com/projectdiscovery |
| **httpx** | ProjectDiscovery | MIT | `go install -v github.com/projectdiscovery/httpx/cmd/httpx@latest` | No | Verified — github.com/projectdiscovery |
| **katana** | ProjectDiscovery | MIT | `go install github.com/projectdiscovery/katana/cmd/katana@latest` | No | Verified — github.com/projectdiscovery |
| **nuclei** | ProjectDiscovery | MIT | `go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest` | No; nuclei-templates repo is free/community-curated | Verified — github.com/projectdiscovery |
| **interactsh** | ProjectDiscovery | MIT | `go install -v github.com/projectdiscovery/interactsh/cmd/interactsh-client@latest` | No (public interactsh server free; can self-host) | Verified — github.com/projectdiscovery |
| **amass** | OWASP | Apache-2.0 | `go install -v github.com/owasp-amass/amass/v4/...@master` | Optional free API keys for more data sources | Verified — github.com/owasp-amass |

- Docker and package managers (Homebrew, apt via Kali/ParrotOS) are alternative install paths for all of the above.
- **ProjectDiscovery Cloud Platform (PDCP)** is a paid/freemium hosted tier (dashboards, cloud scanning, storage). **The CLI tools remain fully free and open-source** — the cloud platform is optional and does not gate the binaries. Some CLIs offer optional free PDCP API-key integration for extra features, but core functionality works without it.

---

## Section 4 — Attack / Offensive Tooling & Scan Engines
_Added 18 July 2026 (separate deep-research pass). Same discipline: vendor pages where reachable; marked Partially verified where pricing sat behind JS/login or came only from third-party reviews._

### Internet scan engines
- **Shodan** — Free registration allows basic searches (first 2–3 result pages). Full use needs a one-time **Membership upgrade ($49)** → 100 query credits/month + 100 scan credits (10k results per 100 credits). **Freelancer $69/mo** (10,000 query credits, 5,120 scan credits/mo); higher: Small Business $359, Corporate $1,099. **Fit:** industry-standard for finding exposed services; the $49 membership is usually enough solo. *(Verified)*
- **Censys** — Free tier: 100 credits/month (1 credit per query). Buy more credits (bundles from $100) to enter the Starter tier; credit-based, no fixed monthly fee. **Fit:** strong for certificate/host searches; free credits suit occasional lookups, serious use needs credit packs. *(Verified)*
- **FOFA** — Registered (free): 300 query credits/month (~3k results). Cheapest paid **Personal $49/mo** ($25/mo billed annually) → 10,000 queries, up to 100k results/month. Higher: Professional $149/mo, Business $1,499. **Fit:** specialized in Chinese/Asia targets; start free, upgrade to Personal for ~10k queries/mo. *(Verified)*
- **ZoomEye** — Free (after signup): up to 10,000 results/month, monitor up to 50 IPs. Paid from ~$70/month (~30k results, 256 IP monitors). **Fit:** broad port coverage (~3,828 ports) + web content; complements Shodan for China/Asia targets. *(Partially verified — from a comparative review; no official pricing page found)*
- **Netlas** — Free Community (personal): 50 requests/day, ~2.5k results/month. **Freelancer $49/mo** ($40.83/mo annual) → 1,000 req/day, up to 1M results/month. Higher: Business $249, Corporate $830. **Fit:** modern OSINT platform with high data volumes; free tier limited, $49/mo generous for solo. *(Verified)* — see also Section 1 (Netlas also does WHOIS/domain-intel).

### Web-attack tooling
- **Burp Suite Professional** — **$499/user/year**. No free full tier (Community Edition is free but manual-only). **Fit:** the de facto premium web-pentest suite — automated scanning + manual exploitation. *(Verified)*
- **Caido** — Web proxy/fuzzer. **Basic = Free** (2 projects, 7 workflows). Paid Individual/Team (unlimited projects/workflows) — exact USD not listed (~$20–40/mo Individual by region). Not open source. **Fit:** programmable attack proxy/replay; prototype on free, upgrade for heavy automation. *(Partially verified — free vs unlimited feature set listed, no price)*
- **sqlmap** — Free, OSS (GPLv2). SQLi exploitation / DB takeover. *(Verified)*
- **ffuf** — Free, OSS (MIT). Fast content/endpoint fuzzing. *(Verified)*
- **wfuzz** — Free, OSS (GPL2). Flexible parameter/form fuzzer. *(Verified)*
- **DalFox** — Free, OSS (MIT). Automated XSS discovery/verification. *(Verified)*
- **Nuclei** — Free, OSS (MIT). Templated vuln scanner (also in Section 3). *(Verified)*

### Paid APIs / hosted services
- **Fuzze.rs (managed fuzzing)** — from **$179/mo** (Starter, 8 cores), **$349/mo** (Professional, 16 cores); no free tier. **Fit:** large-scale binary fuzzing (AFL++/libFuzzer) — overkill for solo web bug bounty. *(Verified)*
- **ProjectDiscovery Cloud ("Neo")** — pay-as-you-go platform (Nuclei, API pentests, etc.), from **$250 for 50 credits** (~$5/credit). No standing free tier (trial may exist). **Fit:** on-demand cloud scanning without managing infra. *(Verified)*
- **OOB / Interactsh** — OSS, no cost. Public servers (oast.fun, oast.live) free for callback handling; no paid hosted plan. **Fit:** DNS/HTTP/SMTP callbacks for OOB/blind vulns; self-host or use public instances. *(Verified)*

### What's worth paying for first on the attack side (solo)
Start with the essentials: **Burp Suite Pro (~$499/yr)** if you're doing serious manual + automated web testing, and **Shodan Membership ($49 one-time)** for network recon. For scan data, exhaust free tiers first (Netlas Community, FOFA Registered, ZoomEye free, Censys 100 credits/mo) before a $49/mo Netlas or FOFA Personal upgrade. Keep all the OSS attack tools (sqlmap, ffuf, wfuzz, dalfox, Nuclei, Interactsh) free. Defer Fuzze.rs and cloud platforms until a specific need. In short: pay first for Burp and maybe one scan subscription; rely on OSS for everything else.

> **Note for Trinity:** none of these attack tools are wired into Trinity's execution layer today (which is LLM / prompt-injection against the range app). This section is a forward-looking inventory for when traditional web-attack tooling gets integrated — not a current dependency.

---

## What could NOT be verified from a primary vendor source (flagged)
1. **WhoisXML API — dollar price per DRS credit / smallest paid bundle.** Pricing tables at drs.whoisxmlapi.com/pricing and the DRS credits calculator are JavaScript-rendered and/or behind login; no vendor page or reliable secondary source exposes the actual dollar figure. Only the credit-consumption model (Reverse WHOIS = 1 credit) and the 500-free-credit tier are vendor-verified.
2. **SecurityTrails — the 50 queries/month free tier.** Not displayed on the current vendor pricing page; only attested by 2026 third-party sites. Paid self-serve tiers ($500/mo, $1,500/mo) and Enterprise "contact sales" ARE vendor-verified.
3. **SecurityTrails OSINT Toolkit price** (2,500 queries/mo) — plan exists on the vendor page but the dollar price is not shown (described only as "an amazing discount").
4. **Netlas.io exact paid tier dollar figures** — the plan comparison at app.netlas.io/plans is JavaScript-rendered; only the free Community tier (50 requests/day) and the "$49/mo starting" figure (third-party) could be captured. Search-coin model is vendor-verified.
5. **DeepSeek V4 off-peak discount** — the historical 50–75% off-peak window is documented for V3/R1 but not confirmed by the vendor for V4.
6. **All "Enterprise / contact sales" pricing** (SecurityTrails Enterprise, WhoisXML enterprise packages, Netlas Enterprise, Groq enterprise-only models like Minimax M2.5 and Qwen3-VL 32B) — no public figures.

## What to buy first, and the cost math (solo hunter)
**Buy nothing in month one — start entirely free, then spend ~$10–$15 only if recall justifies it.** Month one: install the free OSS stack (subfinder + httpx + katana + nuclei + amass, $0), sign up for **WhoisFreaks' 500 free reverse-WHOIS credits** and **Whoxy's** pay-as-you-go account, and use crt.sh + Chaos for subdomain/cert pivots at $0. For LLM work, use **Groq's free tier** (30 rpm/1,000 rpd) and **Gemini AI Studio's free tier** to build your classification + judgment pipeline at $0. If your targets need more reverse-WHOIS volume than the free credits cover, **buy one Whoxy bundle: $10 for 1,000 reverse-WHOIS queries** (each returning up to 1,000 domains in mini mode = up to ~1M domain rows for $10, and $0 on empty results). That single $10 top-up plus **~$1–$3/month of tiered LLM spend** (Groq 8B volume + Gemini 3.1 Pro or DeepSeek V4 Pro frontier on 10M+500K tokens) covers a serious solo recon operation for **under $15/month all-in**. Skip SecurityTrails ($500/mo floor), DomainTools/RiskIQ (enterprise), and WhoisXML (opaque credit pricing) — none earn their cost for a solo budget when Whoxy delivers the same GDPR-limited WHOIS data an order of magnitude cheaper. And remember the hard ceiling: because ~86% of post-2018 WHOIS records are redacted (Interisle 2024), no amount of reverse-WHOIS spend recovers most modern domains — put your effort (not your dollars) into CT-log, reverse-IP, and reverse-NS pivots.
