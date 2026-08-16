# ReconRelate Resource Ecosystem - Complete API Reference

This document categorizes all high-quality free, generously-tiered, and enterprise OSINT APIs and datasets. It's organized by use-case and includes a complete reference of what's available vs. what's actually used in ReconRelate.

---

## Table of Contents
1. [Domain & Subdomain Enumeration (Passive DNS)](#1-domain--subdomain-enumeration-passive-dns)
2. [Infrastructure & Hosting Pivoting (Reverse IP, NS, MX)](#2-infrastructure--hosting-pivoting)
3. [Registration Data (WHOIS & RDAP)](#3-registration-data)
4. [Web Technology & Tracking IDs (Shadow Infra)](#4-web-technology--tracking-ids)
5. [Threat Intelligence & Reputation](#5-threat-intelligence--reputation)
6. [Summary: Free vs Paid vs Used in ReconRelate](#6-summary-free-vs-paid-vs-used-in-reconrelate)

---

## 1. Domain & Subdomain Enumeration (Passive DNS)
*Used for finding associated hostnames, subdomains, and historical DNS records.*

### Available Sources

| Source | Cost | Status | Notes |
|--------|------|--------|-------|
| **Rapid7 Project Sonar FDNS** | Free (bulk download) | Not Used (requires local storage) | Massive historical DNS dataset. Best for offline indexing. |
| **CIRCL Passive DNS (pDNS)** | Free (partners only) | Not Used | Historical DNS records via JSON API. |
| **SecurityTrails** | Limited Free / Paid | Not Used | Industry standard for historical pivoting. |
| **HackerTarget** | Free (basic) | ✅ **USED** | Subdomain enumeration via `hostsearch` API. Rate: ~100 queries/day. |
| **crt.sh (Certificate Transparency)** | Free | ✅ **USED** | Extract subdomains from SSL/TLS certs. Extremely reliable for active subdomains. |
| **Google BigQuery DNS** | Free tier | Not Used | Requires GCP project setup. |

### How We Use It
```python
# Subdomain finding pipeline:
1. crt.sh → Primary subdomain discovery (fast, cert-based)
2. HackerTarget → Fallback when crt.sh is down
```

---

## 2. Infrastructure & Hosting Pivoting (Reverse IP, NS, MX)
*Used for finding domains that share the same IP, nameservers, or mail servers.*

### Available Sources

| Source | Cost | Status | Notes |
|--------|------|--------|-------|
| **DNSlytics API** | 2,500 req/day free | Not Used | Reverse IP, NS, MX lookups. |
| **DomScan API** | 10,000 credits/month free | Not Used | DNS + Reverse NS lookups. |
| **AlienVault OTX** | Free (account) | Not Used | Passive DNS + IOC relationships. |

---

## 3. Registration Data (WHOIS & RDAP)
*Used to find ownership details, organization names, and other domains owned by the same entity.*

### Available Sources

| Source | Cost | Status | Notes |
|--------|------|--------|-------|
| **Direct RDAP Registries** | Free | Not Used | IANA bootstrap servers. Requires caching/backoff. |
| **WhoisJSON** | ~1,000 req/month free | Not Used | Normalized WHOIS/RDAP JSON. |
| **IP2WHOIS** | ~500 req/month free | Not Used | Normalized WHOIS/RDAP. |
| **python-whois** | Free | ✅ **USED** | Local WHOIS lookup via `whois.whois()` (raw text parsing). |

### How We Use It
```python
# Registration data pipeline:
python-whois → Local WHOIS lookups (raw text extraction)
→ Normalize registrant emails, nameservers, creation dates
```

---

## 4. Web Technology & Tracking IDs (Shadow Infra)
*Used to correlate domains owned by the same company through shared Google Analytics IDs, AdSense, or software stacks.*

### Available Sources

| Source | Cost | Status | Notes |
|--------|------|--------|-------|
| **DNSlytics Reverse Analytics/AdSense** | Within 2,500 daily limit | Not Used | Cluster domains by GA/AdSense IDs. |
| **Wappalyzer API** | ~1,000 req/month free | Not Used | Technology fingerprinting from HTTP. |
| **BuiltWith** | Free UI, Paid API | Not Used | Historical tech tracking. |
| **Host.io** | ~1,000 req/month free | Not Used | Basic tech detection + backlinks. |

---

## 5. Threat Intelligence & Reputation
*Used to annotate domains or IPs with "abuse" or "malice" scores, helping the AI prioritize risky infrastructure.*

### Available Sources

| Source | Cost | Status | Notes |
|--------|------|--------|-------|
| **VirusTotal** | 500 req/day, 4/min | Not Used | AV verdicts + domain-file relationships. |
| **AbuseIPDB** | 1,000 checks/day free | Not Used | Crowdsourced abusive IP reports. |
| **urlscan.io** | Free, generous limits | Not Used | IOC extraction from browser loads. |
| **GreyNoise** | ~50 searches/week | Not Used | Scanner identification. |
| **Shodan** | ~100 credits/month | Not Used | Port scans + service banners. |
| **Censys** | 250 calls/month free | Not Used | Host/cert data. |
| **AlienVault OTX** | Free (account) | Not Used | Community threat pulses. |

---

## 6. Summary: Free vs Paid vs Used in ReconRelate

### ✅ Currently Used in ReconRelate

| Category | Tool/API | Cost Model | Quota/Limit |
|----------|----------|------------|-------------|
| **Subdomain Enumeration** | crt.sh | Free | Unlimited (streamed, truncated for large sites) |
| **Subdomain Enumeration (Fallback)** | HackerTarget | Free | ~100 queries/day |
| **DNS Resolution** | python socket/dnspython | Free | No limit |
| **WHOIS Lookups** | python-whois | Free | Depends on registry (rate-limited) |
| **Reverse WHOIS (Web Search)** | DuckDuckGo-search | Free | ~45 queries/min |

### 🆓 Available Free (Not Currently Used)

| Category | Tool/API | Quota/Rate | Best For |
|----------|----------|------------|----------|
| **DNS History** | Rapid7 Sonar FDNS | Bulk download (login required) | Historical passive DNS indexing |
| **Reverse Lookups** | DNSlytics API | 2,500 req/day | Reverse IP, NS, MX, Analytics |
| **Threat Intel** | AbuseIPDB | 1,000 checks/day | IP reputation scoring |
| **Threat Intel** | urlscan.io | Per-action limits | IOC extraction from page loads |
| **Tech Detection** | Wappalyzer API | ~1,000 req/month | Tech stack fingerprinting |
| **WHOIS Normalized** | WhoisJSON | ~1,000 req/month | Clean WHOIS data |

### 💰 Paid/Enterprise (Not Used)

| Tool | Typical Cost | Why Not Used |
|------|--------------|---------------|
| SecurityTrails | $$$ | Limited free tier, expensive |
| DomainTools | $$$ | Overkill for current needs |
| VirusTotal Premium | $$$ | Free tier sufficient for targeted checks |
| Shodan Premium | $$$ | OSS tier covers basic needs |

---

## Implementation Notes

### Rate Limiting Strategy
```python
# We implement exponential backoff for all HTTP clients
# to respect rate limits:
- HackerTarget: ~100/day → 1 request per hour max
- crt.sh: Stream with 1MB response cap
- python-whois: Per-registry limits (typically 5-20/min)
- DuckDuckGo: ~45/min → we batch queries
```

### Fallback Pipeline
```
Primary: crt.sh → Subdomain discovery
   ↓ (timeout/error)
Fallback: HackerTarget → Additional subdomains
   ↓ (still no results)
Manual: python-socket → Basic DNS resolution
   ↓ (WHOIS needed)
Fallback: python-whois → Registration data
```

---

*Last updated: 2026-04-21*
