# Standalone native profile service

This is the private server component for the NetEase-only website. It is not
deployed yet. A server running this code uses normal authorized SDK login and
the two packaged public manifests; it does not read an installed game, Steam,
browser storage, SDK logs, packet captures or process memory.

Current support is the reviewed PC build `3848703` and one explicitly
authorized login owner, UID `1370598422`. That owner can request other UIDs
through the reviewed player-lookup operations and their visibility checks.
It is not a general multi-account login service. An arbitrary target UID does
not need to belong to a local account.

## Deployment prerequisites

- An approved independent host with Python 3.11+, persistent local disk,
  outbound HTTPS and native TCP, and an HTTPS reverse proxy. Sites cannot run
  the native TCP client itself. No host has been purchased or configured.
- Install `requirements-native-client.txt` in that host's dedicated environment.
  The HTTP listener uses Python's standard library. Keep the source modules and
  `runtime-inputs/3848703.json` and `runtime-inputs/3848703-social.json` together.
- Supply `NETEASE_SESSION_KEY` as a base64-encoded 32-byte key from the host's
  secret manager, separately from the database and its backups.
- Supply `NETEASE_API_TOKEN` as a high-entropy, printable ASCII bearer secret
  of at least 32 characters. Only the website's server-side provider and the
  native server need it; never put it in frontend code or player responses.
- Choose an explicit database path on a private persistent volume outside the
  source checkout. Its parent directory must already exist. One service
  process owns that path; do not run replicas or restore stale session backups
  as a fallback for a failed token rotation.

Neither secret has a built-in default. Do not paste real credentials into chat,
commit them, put them in command arguments, or embed them in a source archive.
Existing environment-specific credential and hosting approval is still needed
before any real bootstrap or deployment.

## Portable source package

From the trusted development checkout, create a new ZIP at an explicit path:

```text
python -B scripts/package-native-api.py --output /existing/output/native-api-3848703.zip
```

The packager includes an explicit 33-file runtime allowlist: 28 shared Python
modules, the serving CLI, pinned requirements, this guide and both current
public manifests. Some shared wire modules retain historical `_probe` names;
their file-analysis entry points are not used by the service. Game archives,
native executables, research scripts, tests, browser data, session databases,
environment files and credentials are not included.

It verifies both public-input hashes, rejects links/reparse points and special
files, and refuses to overwrite an existing ZIP. Before saving, it extracts
the ZIP into a temporary directory and imports the runtime closure there, then
runs `serve-native-api.py --check` with desktop/research dependencies and
network entry points blocked. A missing runtime dependency fails packaging.
The packager must itself run in an environment with the native requirements
installed; it does not install or bundle an interpreter or third-party wheels.

The output report supplies the ZIP SHA256, size, file count and tested platform.
Extract the package on the approved host, install its requirements and repeat
`--check` there. A Windows extraction check does not establish Linux operation,
live authentication or cloud-IP acceptance. The packager executes only our
trusted Python sources for its smoke check; it is not a sandbox for untrusted
checkouts. Ordinary source archives are not credential backups.

## Commands on the approved host

Validate the packaged inputs without credentials, persistence or network:

```text
python -B scripts/serve-native-api.py --check
```

An explicit one-time bootstrap consumes a single JSON document from standard
input and exits. The supplied document uses the already reviewed SDK handoff
shape: `selected_uid`, `sauth`, `saved_account`, `mpay_context`, `wrapper_udid`.
It must be obtained from the owner's authorized login, not fabricated from the
target UID. The bootstrap is a one-time operator setup, not a second step for
each website lookup. It validates the shape/binding but does not itself perform
live authentication. Existing configured state is never overwritten.

```text
python -B scripts/serve-native-api.py --database /private/native-session.sqlite --initialize-from-stdin
python -B scripts/serve-native-api.py --database /private/native-session.sqlite --port 8080
```

Use the host's secret-injection/stdin mechanism; these commands intentionally
do not show or generate credentials. The first command requires the encryption
key. Normal serving also requires the API bearer token. The database stores
AES-GCM encrypted credentials; only owner UID and revision metadata are plain.

The listener defaults to `127.0.0.1`. Put the approved host's HTTPS reverse
proxy in front of it. Forward `Authorization` privately and avoid logging its
value. Do not point the website at the user's laptop or create a laptop tunnel.

For a managed host whose proxy requires an all-interface listener, serving can
explicitly use `--bind-host 0.0.0.0` and the host-assigned `--port`. For example,
Render requires that bind address and recommends its `PORT` value; its proxy
terminates HTTPS before forwarding to the service. See the official
[Render port-binding documentation](https://render.com/docs/web-services#port-binding).
No Render service is selected or configured by this option.

Only `127.0.0.1` and `0.0.0.0` are accepted; the bind option is rejected for
`--check`, bootstrap and recovery because those commands have no listener.
`HOST`/`PORT` environment variables never silently override the defaults.
Pass the assigned port explicitly in the approved host's start command. Both
bind modes still require the encryption key and private API bearer. Do not
run the all-interface option on the user's laptop or without the intended
host's managed proxy/network boundary.

## Host/proxy acceptance checklist

- Use one persistent service process and one replica, with an existing writable
  database parent directory on persistent storage. SQLite journal and OS lock
  semantics must work there; an ephemeral filesystem or multiple independently
  restored copies is not a session store. Keep the same separately stored
  encryption key after restart.
- Permit direct outbound HTTPS for normal SDK/public-list operations and native
  TCP to the officially returned LOGIN/gateway/Social endpoints. The reviewed
  clients do not use environment HTTP proxies; an HTTP-only egress proxy is not
  equivalent to the required TCP connectivity. Verify cloud-IP acceptance live.
- Preserve `Authorization`, the exact request path and lookup query, JSON error
  bodies, `lookupId` and `Retry-After`. Do not replace native `503` pending replies
  with a proxy error page. Allow more than the service's 15-second HTTP wait so
  the browser receives the pending job reference instead of a proxy timeout.
- `/health` requires the bearer and proves only listener readiness. Use an
  authenticated HTTP health check or a TCP port probe; an unauthenticated HTTP
  check will receive `401`. Neither probe proves valid NetEase authentication.
- Bootstrap only on the actual mounted runtime volume, never in a build image
  or a separate pre-deploy filesystem. Render's pre-deploy command, for example,
  cannot access the attached persistent disk; see its official
  [deploy-command documentation](https://render.com/docs/deploys#pre-deploy-command).
- Configure termination to let the current worker drain before forced kill.
  Validate this on the chosen host. Unknown interrupted rotation is handled as
  uncertain, not repaired by replaying an older refresh token or restoring an
  older database backup. No zero-downtime/replica assumption applies to this
  single-owner session service.

## HTTP contract

Authenticated `GET /v1/players/{uid}` returns a bounded `schemaVersion: 2`
NetEase envelope accepted by the website's `parsePlayer` adapter. The UID is
validated exactly; output contains only projected identity/display statistics,
not credentials, raw packets, callback objects or upstream diagnostic text.

Each section independently respects visibility. Owner season history is read
from exact-UID/zone-bound startup properties, with a bounded parser preserving
it only across source-reviewed unrelated updates. Rank/identity changes,
unknown updates and lifecycle invalidation make that section unavailable;
other independently verified sections survive. The September 12 authorized
live owner bundle returned 12 season rows through the complete startup/panel
window. This is not a completeness, hosted or long-term renewal claim.
Matches are at most the first 50, not lifetime history.
Numeric rank levels and raw server scores are not guessed English rank names
or normalized points. A match without a stable ID retains its basic statistics
but does not expose an unbindable rank change. Zero optional rank-update times
become unknown, not a fabricated date. No punishments feed is enabled; see
`PUNISHMENT_RESEARCH.md` for the source and coverage limitations.

One worker serializes session use. Concurrent requests for the same UID share
one operation. Up to four distinct UIDs may be pending. Successful projected
results are cached for 30 seconds, bounded to 100 entries. HTTP handlers wait
at most 15 seconds, then return `503`, code `lookup_pending`, an opaque
`lookupId`, and `Retry-After: 2`. Resume with
`GET /v1/players/{uid}?lookup={lookupId}`. That request observes only the
original UID-bound operation, including its terminal failure; it never starts
another lookup or token exchange. Successes and failures remain available for
up to 300 seconds after completion in a separate 100-job bounded registry;
completed entries may be evicted for capacity. Missing/expired/wrong-UID IDs
return `410 lookup_expired`, never fall back to a new operation or UID cache.
A browser leaving or timing out does not cancel credential persistence. `lookup_busy` also has
a retry hint. Other fixed failures must not be treated as an invalid player
or automatically retried indefinitely.

The website now automatically observes these job references for up to 90 seconds
after one submission. Editing the UID or choosing Stop waiting cancels only the
browser wait. Terminal failures/expiry never silently initialize another job.
There is no whole-lookup time SLA: network stages have their existing bounded
timeouts, and HTTP wait time is not a native-work cancellation deadline.

Authenticated `GET /health` reports listener/service readiness only, not live
NetEase reachability or authenticated-session validity. There are no HTTP login,
bootstrap, recovery, credential-download, report-submission or admin endpoints.

## Durable renewal and recovery

The SDK response does not provide a verified access-token TTL. Therefore an
uncached ready-state lookup performs one normal saved-account refresh exchange;
no timer claims to know expiry. It persists `refresh_in_flight` before that
exchange, saves both replacement tokens before AUTH, and promotes the session
only on exact owner authentication, even if the target's stats are unavailable.

After a restart, a saved candidate can be verified without repeating the SDK
exchange. An unknown exchange result or crash before the returned candidate is
saved does not justify retrying the old token. Only a correlated normal LOGIN
AUTH rejection `253` permits the *next* lookup to use the latest saved refresh
token; it neither proves expiry nor triggers a recursive retry. Other rejections
and unknown verification failures retain the candidate for explicit recovery.

Stop the service before the operator recovery command. It acquires the same
exclusive OS lock, queries only the owner, and does not exchange the SDK token:

```text
python -B scripts/serve-native-api.py --database /private/native-session.sqlite --recover-pending
```

Unknown rotation without a saved candidate cannot be repaired by this command.
Do not delete state, overwrite it with an older backup, or reuse the preceding
refresh token to hide that outcome. It requires a newly authorized login and
an explicit operator recovery decision.

Normal SIGINT/SIGTERM stops accepting requests and drains the worker before
releasing ownership. Configure the host to allow that drain; force-kill, power
loss and a short supervisor stop timeout can still require recovery. Handler
disconnects do not cancel work, and no second service may steal an active lock.

## Verification and remaining acceptance

Synthetic tests cover the real encrypted SQLite store, fresh-instance restart,
CAS and exclusive ownership, token ordering, classified failures, HTTP
singleflight/cache/timeouts/disconnects, exact UID projection and CLI shutdown.
The portability smoke test blocks desktop and research-only imports. These
checks ran on Windows; Linux acceptance is not yet verified. Live-verified,
projected owner/other profile bundles pass
the new Python envelope and the website's TypeScript parser without new
NetEase calls or deployed snapshots.

Five additional real-process tests in `tests/standalone_session_process_test.py`
use only synthetic credentials and block child socket operations. On Windows,
the parent force-terminates a live child holding the actual owner lock at
`candidate_pending`, `candidate_verifying`, uncertain `refresh_in_flight`, and
an uncommitted SQLite CAS. A fresh child can recover the committed candidate
without another SDK exchange; an uncommitted write rolls back and both locks
become usable. A later normal lookup uses the latest saved refresh token.
Uncertain exchange without a saved candidate remains blocked from replay.
This verifies Windows process-exit recovery, not power-loss durability, graceful
Linux signals, cloud-volume behavior or real SDK expiry. No genuine credentials
were persisted by these tests.

Still required: approved independent hosting and real credential bootstrap,
live server restart/refresh verification, connection to the prepared website,
hosted multi-UID/visibility checks. The bounded owner startup decoder plus
reviewed unrelated-sync handling returned 12 verified owner season rows through
the final live startup/panel window. The same result passed the website parser;
this establishes the decoder/contract, not production hosting.
Passing offline tests is not proof of live session expiry/renewal or completion
of the standalone hosted goal.
