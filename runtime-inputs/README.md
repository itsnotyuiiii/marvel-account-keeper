# Reviewed public runtime inputs

September12 continuation: unchanged3848703 inputs now also support LIVE19
other-player rank seasons, two normal returned-refresh-token exchanges with
fresh owner AUTH/gateway, and the normal owner current-rank panel. Actual panel
season20 differs from the other's last historical season19. Numeric owner rank
level14/raw score4350 is verified; English labels/display normalization are not.
Renewal chaining is verified, but expiry/restart persistence/hosted operation
remain incomplete. No runtime artifact, pin, game-file dependency or production
provider changed. See game_rank_panel_probe.py and web_sdk_history_chain.py.

September12 follow-up: the unchanged current3848703 public artifacts now also
support LIVE verified mesh identities/visibility for owner1370598422 and other
1822797559, own XYQ Career and other native Career. Both aggregate responses
were exact-target bound and projected into five totals. The explicit other
path reuses the same accepted owner gateway after fresh mesh overview checks;
it does not require the target to exist locally. Research flags are additive;
no desktop/website provider default, runtime artifact or pin changed. This
verifies query portability, not autonomous credential renewal or hosting.
See API_RESEARCH.md for source contracts and exact live evidence.

The same unchanged inputs subsequently verified43 first-page other-player
matches and43 per-match rank changes. Source-aligned int(new_level) conversion
resolved a local strict-integer false-negative; the live field is float-typed.
This is independent match/UID-bound data, not reconstructed rank snapshots.

## Additional current SocialMatrix artifact

`3848703-social.json` is 465 bytes, ASCII with LF and final newline; SHA256
`c3d1a44164613862529a63f4598b01af969dcb2b881107ae12c5edbb0eae09e4` is pinned
independently in `portable_social_runtime.py`. It contains only current archive
provenance, game ID, reviewed token RPC index1538836248 and the public17-byte
SDK parameter from entry5512, Scripts/share/const/const_social.py. Parameter
SHA256 is `e7a322fd20ed8591c366213d4d4d9ca579a5c2d4e0ca11c8e7f63367c7aa5209`.
It contains no account/route/token/template or executable code. Loading reads
only the explicit file, with no installed-file or network fallback.

The normal token RPC was then received on the exact-owner live gateway and its
owner/zone/time/route claims validated. This supersedes the initial-token/index
uncertainty in the prior checkpoint below, but does not prove SocialMatrix
service acceptance by itself. A separate subsequent live normal Python setup
did verify service acceptance, exact RPC/aid binding and seven advertised
modules including svc-user, using this artifact and the fresh pushed token.
The adapter uses current source-reviewed hello/setup and does not load the old
native/captured uni_lz4 template. No player query or automatic OAuth renewal is
verified by setup. See API_RESEARCH.md for the complete live result and parser
corrections; all sockets/helper are terminal.

## Current reviewed build: 3848703 (September 12, 2026)

`3848703.json` is a separate public-only 774-byte ASCII JSON artifact with LF
endings and a final newline. It contains transport key/parameter, Career RPC
indices, source provenance, game ID and the official login-list URL, with no
account IDs, credentials, local paths or game code. Its independently reviewed
SHA256 is `0111accea3a87a8ed6f45190f41cf5e517b400944c6342bdf689afa34bbb1d81`
(`CURRENT_MANIFEST_SHA256` in `portable_game_runtime.py`).

The completed Steam update has installed/target build 25249397, but the reviewed
native version is 1.1.3848703 / changelist 3848703. Its source provenance is:

- Archive SHA256: `07a3dcf20d539b9a613246047f5f91e4c7b44de62e96e24c47acea1cef5d1f9f`.
- Native image SHA256: `f9b9afda45adbb8db8162c12906d79d82fe627afecab5c0c3bbf0b5edcea2612`.
- Public version.json SHA256: `a83bba27b4c37f885561662ebae0e2cd197f400defb0eb6dfa4aa39ff73eb951`.

Independent static review validated the RSA assignment at core/bootstrap.py
entry 3188 and default RPC salt at 5781. The archive-decoding redirect.py
bootstrap is a distinct entry, 4764. Career request/reply IDs remain 113612951 /
1993623137. The LOGIN protocol at 5112 has identical typed PREPARE, AUTH-request,
AUTH-reply, ENTER and LOGIN-reply descriptors. The RSA DER, 18-byte handshake
parameter and official server-list URL are unchanged; this was verified, not
inferred from a matching version label. The parameter moved to native
RVA 0xb616098 / file offset 0xb615298; old RVA 0xb41fb78 is not valid for this build.
The updated SDK's SDC_LOG_OS_NAME/pc strings moved to 0x4b0648/0x4b0658; old SDK
RVAs 0x4b07a8/0x4b07b8 are not valid. Exact descriptor, SDK and config hashes and
source windows are recorded in the September 12 API_RESEARCH.md checkpoint.

The artifact and provenance pin are additive. `3825464.json`, its constants and
all existing defaults remain unchanged. `reviewed_runtime_pin("3848703")`
returns the separately reviewed current provenance/digest; unsupported versions
fail without input-derived trust or installed-file/network reads. The research
helper selects it explicitly with `--runtime-version 3848703`. The existing
installed-source exporter is not an automatic new-build reviewer or repinner.

One live AUTH-only test used the current packaged artifact and the owner's
existing official-store SDK session with reviewed current versions and outer
os_name=pc. It returned `own_existing_role_verified` for expected UID 1370598422:
prepare, AUTH-service acceptance and own-role verification true, one connection,
286 handshake + 625 application bytes sent, 2399 received, then closed. An earlier
helper expired before payload reading and sent no AUTH. No gateway entry, game
login, Career/punishment query, refresh or saved credentials followed. No game
files were read by the live helper; source review was a separate static step.
This resolves rejection of this tested envelope, not independent renewal or
full-stat service completion. That AUTH-only checkpoint passed 32 web-SDK tests.

A separate research-only `--enter` follow-up selected the same current version
and PC envelope and returned `own_gateway_login_verified`. Prepare, AUTH-service,
own-role and game-login verification were true. The LOGIN leg sent 286 handshake
bytes plus 625 application bytes and received 2397; the separate gateway sent 286
handshake + 71 application bytes and received 39295, each on one connection.
The gateway matched expected UID 1370598422, verified login_key presence without
export, and returned reply_relogin=0. Four HOTFIX records (37886 content bytes)
were received but never executed. Both sockets/helper closed with no after_login
callback, Career/SocialMatrix/punishment query, refresh or saved credentials.
Current gateway login is now verified, not independent renewal or live stats.

The final focused set passes 50 combined web-SDK tests in 0.097s and 40 runtime
tests in 0.129s. The complete Python suite after all current source edits passed
572 tests in 69.995s, with existing non-failing synthetic warning/error outputs
and ResourceWarnings.
No production provider/site or deployment changed. Initial normal SocialMatrix
token acquisition is next; source S_REFRESH_SOCIAL_TOKEN is identified but no
live token has yet been received or verified. Its numeric RPC index and fresh
SocialMatrix hello/setup/routes remain unresolved; see API_RESEARCH.md.

## Earlier reviewed build: 3825464

`3825464.json` contains only the public transport key/parameter, reviewed Career
RPC indices, source provenance, game identifier and official login-list URL.
It contains no account identifiers, sessions, tokens, local paths or game code.
The artifact is 774 bytes of ASCII JSON with LF endings and a final newline.

SHA256: `63d536706d9cb055770129ca413950861d2553cb6c977bdee4f113fa66ccbc28`.
The separately reviewed expected digest is `REVIEWED_MANIFEST_SHA256` in
`portable_game_runtime.py`; do not learn the trusted digest from input data.
`.gitattributes` preserves LF across Windows/Linux checkouts.

### September 11 source review

On September 11, 2026, the installed archive/image still matched the previously
reviewed build pins. Existing hash-gated loaders produced the public material
and Career request/reply indices. The reviewed Shipping config plaintext hash
and exact official URL matched, and public version metadata reported
`1.1.3825464` / changelist `3825464`. These checks establish source provenance,
not compatibility with a later live patch or runtime method overrides.

`scripts/export-public-runtime.py` prepares a deterministic candidate from four
explicit distribution paths (`--archive`, `--image`, `--config-pak`,
`--version-json`). It emits bytes only to stdout after all existing pins and the
strict manifest parser pass. Its digest is candidate metadata, not independent
approval. It does not write files, read account data, launch the game or make
network requests. The version file's public asset inventory is ignored; only
version/changelist are checked. A 256 KiB read bound covers the observed
108,231-byte file without exporting that inventory.

The main review checked the 774 selected bytes and recorded the independent
digest above. No application provider or deployed site was switched to them.
Future builds need explicit source review and new compatibility evidence;
do not update pins automatically or guess version strings.

### September 11 bounded live transport check

September 11 follow-up used only this packaged artifact and one ordinary HTTPS
read of its exact official server-list URL (no redirects/cookies/environment
credentials). The existing parser validated the bounded list. One connection
to the first advertised port completed the existing normal key exchange:
286 handshake bytes sent, 54 bytes received, decrypted main1/sub101 LOGIN
prepare record with a19-byte body. The socket closed immediately afterward.

No installed files, credentials, authentication request, heartbeat, gateway
login or Career request were used. This verifies portable transport to the
then-advertised service, not game-version acceptance, account login, renewal,
RPC compatibility or any profile data. No retry or port scan was performed.

## Runtime use

```python
from pathlib import Path
from portable_game_runtime import (
    REVIEWED_MANIFEST_SHA256, SUPPORTED_PROVENANCE, load_runtime_manifest,
)

public = load_runtime_manifest(
    Path(__file__).resolve().parent / "runtime-inputs" / "3825464.json",
    expected_sha256=REVIEWED_MANIFEST_SHA256,
    expected_provenance=SUPPORTED_PROVENANCE,
)
```

That example deliberately preserves the old explicit build. To select the
separately reviewed current artifact without changing defaults:

```python
from portable_game_runtime import reviewed_runtime_pin

provenance, digest = reviewed_runtime_pin("3848703")
public = load_runtime_manifest(
    Path(__file__).resolve().parent / "runtime-inputs" / "3848703.json",
    expected_sha256=digest,
    expected_provenance=provenance,
)
```

`public.configuration` and `public.career_methods` fit the existing explicit
`GameCareerClient` provider callbacks. Loading this artifact reads only its
explicit packaged file; it does not consult an installed game. A future HTTPS
server-list provider must fetch only `public.login_list_url` and validate the
bounded response with `project_login_server`.

This is **configuration portability**, not a standalone stats service.
Current-build AUTH and gateway login are verified only for the bounded
owner-session experiments above. Independent normal account acquisition/renewal,
SocialMatrix session acquisition, authorized hosting, and live
profile section verification are still required. No credentials are included
or generated by either example; no game connection occurs.
