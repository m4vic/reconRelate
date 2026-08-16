# ReconRelate User Guide

ReconRelate is an intelligent, highly concurrent OSINT (Open-Source Intelligence) pipeline designed to map out domain infrastructure and discover hidden relationships. It uses traditional data gathering techniques (WHOIS, DNS, crt.sh, HackerTarget) and feeds the results into a Local LLM to analyze and extract high-confidence "pivots" (shared emails, orgs, phone numbers) which it then automatically pivots on to find highly-correlated domains.

---

## 🛠️ Prerequisites

1. **Python 3.10+**
2. **Ollama**: ReconRelate uses local LLMs by default to process relations securely and without API costs. Make sure Ollama is installed and running locally on `http://localhost:11434`.
3. **Required Model**: Pull your default model of choice. (e.g. `ollama pull qwen3.5:9b`)
4. **Optional Fast Model**: For speed-critical runs, pull a small model too: `ollama pull qwen2.5:1.5b`

---

## 🚀 Basic Usage

```bash
reconrelate <target_domain>
```

**Example:**
```bash
reconrelate run google.com --max-depth 2
```
This will run the pipeline against `google.com`, searching out up to 2 "hops" away from the original domain.

### Live Status Display
While running, a live status line updates in your terminal:
```
[Depth: 1] | Steps: 12 | LLM Calls: 10 | Domains Found: 87 | markmonitor.com...
```

### Output
Upon completion, the tool dumps artifacts into the `./artifacts` directory.
It also provides a `Run ID` that you can use to visualize the infrastructure graph.

---

## ⚙️ Run Modes

ReconRelate has two scan presets. **Deep is the default** — the tool's purpose is thorough mapping.

| Mode | Use Case | Behavior |
|------|----------|----------|
| `deep` (default) | Full infrastructure mapping | Aggressive subdomain enumeration, large queue, crt.sh-first |
| `quick` | Fast spot-check | Limited subdomains, small queue, HackerTarget-first |

```bash
# Deep scan (default — no flag needed)
reconrelate tesla.com

# Quick spot-check
reconrelate quick tesla.com
```

---

## 🏎️ Dual-Model Architecture

ReconRelate supports two LLM models simultaneously on your GPU:

- **`--model`**: The primary reasoning model (e.g., `qwen3.5:9b`) — used for deep relationship analysis
- **`--fast-model`**: A lightweight model (e.g., `qwen2.5:1.5b`) — ready for quick extraction tasks

```bash
# Use both models: deep thinker + fast extractor
reconrelate run google.com --model qwen3.5:9b --fast-model qwen2.5:1.5b

# Override just the primary model for a faster run
reconrelate quick google.com --model qwen2.5:1.5b
```

You can also set defaults via environment variables:
```bash
set LLM_MODEL=qwen3.5:9b
set FAST_LLM_MODEL=qwen2.5:1.5b
```

---

## ⏸️ Interrupt & Resume

For large domains that may take a long time, you can safely stop and resume runs.

### Stopping a Run
Press **Ctrl+C** at any time. ReconRelate will:
1. Safely commit all work done so far to the database
2. Mark the run as `interrupted`
3. Display a summary of progress

```
⚡ Interrupted! Saving progress for run abc-123 ...
[Depth: 0] | Steps: 347 | LLM Calls: 340 | Domains Found: 1204 | INTERRUPTED...
```

### Resuming a Run
Add `--resume` to pick up exactly where you left off:
```bash
reconrelate run rosie.com --resume
```

The tool will:
1. Find the latest interrupted run for `rosie.com`
2. Skip all already-processed domains
3. Continue from the unprocessed queue

**Bonus**: You can switch models when resuming:
```bash
# Started with a slow deep model, finish with a fast one
reconrelate run rosie.com --resume --model qwen2.5:1.5b
```

Without `--resume`, it always starts a clean new run.

---

## 🔍 Max Depth

Controls how many "hops" away from the root domain the tool will explore.

```bash
# Explore 2 hops deep
reconrelate run google.com --max-depth 2

# Unlimited depth (runs until queue/node limits are hit)
reconrelate google.com
```

When `--max-depth` is omitted, the tool runs indefinitely until either:
- The pending queue is exhausted (no more pivots to chase)
- The global node limit is reached (default: 500 nodes)

---

## 🌲 Visualizing Results

Once a run completes, visualize the pivot tree:

```bash
reconrelate tree <run_id>
```

**Example Output:**
```text
google.com
  [phone] 1997091504
  [org] google llc
    domains.squarespace.com
      [org] squarespace, inc.
      [email] whoisrequest@markmonitor.com
        ip.me
        markmonitor.com
  ...
```

---

## 📊 Reports & Exports

```bash
# Markdown report
reconrelate report <run_id>

# JSON export
reconrelate report <run_id> --format json

# Full artifact bundle (tree + graph + report)
reconrelate export <run_id> --out ./my_output
```

---

## 🎛️ Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_MODEL` | `qwen3.5:9b` | Primary reasoning model |
| `FAST_LLM_MODEL` | *(empty)* | Lightweight fast model |
| `OLLAMA_API_BASE` | `http://localhost:11434` | Ollama server URL |
| `DEFAULT_MAX_DEPTH` | `-1` (unlimited) | Default BFS depth cap |
| `PIVOT_TOP_K` | `5` | Max pivots to chase per domain |
| `GLOBAL_MAX_NODES` | `500` | Hard cap on total graph nodes |
| `LLM_TIMEOUT_SEC` | `60` | Kill LLM calls exceeding this |
| `PER_DOMAIN_TIMEOUT_SEC` | `90` | Safety timeout per domain |
| `RECONRELATE_RUN_MODE` | `deep` | Default run mode preset |
| `RECONRELATE_AUTO_SAVE_ARTIFACTS` | `true` | Auto-save artifacts after run |
| `RECONRELATE_ARTIFACTS_DIR` | `artifacts` | Output directory for artifacts |
