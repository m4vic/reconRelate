# Optional Subfinder integration

ReconRelate can use [ProjectDiscovery Subfinder](https://github.com/projectdiscovery/subfinder) as
its first passive subdomain source. Subfinder is optional: if its executable is unavailable or a run
fails, ReconRelate continues with crt.sh and HackerTarget.

Install Subfinder using the
[official ProjectDiscovery instructions](https://docs.projectdiscovery.io/opensource/subfinder/install),
then verify discovery without making a network call:

```powershell
subfinder -version
reconrelate providers doctor
```

If the executable is not on `PATH`, configure its exact path:

```powershell
$env:RECONRELATE_SUBFINDER_PATH='C:\Tools\subfinder.exe'
reconrelate providers doctor --json
```

ReconRelate invokes the binary without a shell and uses JSONL plus collected source attribution. It
does not enable active resolution, `-all`, or automatic updates. The process has bounded stdout,
stderr, and wall-clock time; timeout or cancellation terminates it. Every source attached to a
hostname becomes a separately attributed observation and evidence link on the resulting claim.

The safe default source set is:

```text
crtsh,alienvault,commoncrawl,waybackarchive
```

Override it only deliberately:

```powershell
$env:RECONRELATE_SUBFINDER_SOURCES='crtsh,github,securitytrails'
$env:RECONRELATE_SUBFINDER_RATE_PER_SECOND='5'
$env:RECONRELATE_PROVIDER_SUBFINDER_TIMEOUT_SEC='25'
```

Subfinder reads its own provider configuration. Some sources require API credentials and may have
paid quotas or restrictive terms. ReconRelate never enables those sources merely because keys exist;
they run only when named in `RECONRELATE_SUBFINDER_SOURCES`. Review the provider's billing and terms
before adding it. ReconRelate counts one Subfinder process invocation as an opaque upstream request;
Subfinder's internal per-source HTTP request count is not available through its JSONL protocol.

Live verification remains explicit and must use an authorized domain:

```powershell
reconrelate providers doctor --live --target example.com
```
