# Owner season-history acquisition: current source evidence

Static-contract and candidate-implementation checkpoint, September 12, 2026
(local time). The attachment and wire-format paths below have been traced, and
a bounded named-property decoder, owner-rank observer and startup hooks are
implemented with synthetic tests. **Actual live owner-history delivery remains
unverified.** This document does not report the result of a pending live attempt
or establish a deployed owner-history feature. Unsupported input remains an
unavailable section, not evidence of an invalid player UID.

## Provenance and scope

The inspected public distribution is native build `3848703`:

- Script archive SHA256:
  `07a3dcf20d539b9a613246047f5f91e4c7b44de62e96e24c47acea1cef5d1f9f`.
- Native image SHA256:
  `f9b9afda45adbb8db8162c12906d79d82fe627afecab5c0c3bbf0b5edcea2612`.
- The archive was decoded in memory using the reviewed `redirect.py` entry
  4764 and the existing inspection helper, with the exact archive hash checked.
  Recovered code was not executed, imported or written out.
- Native methods were located through their public Python binding names and
  inspected as on-disk x64 instructions, not loaded into a game process.
  Those static inspections did not read account records, credentials, captures
  or live connections.

Offsets below are bytecode offsets within the named function, or native RVAs
when prefixed `0x`. They are specific to the pinned distribution.

## Why the owner needs a different reader

Current entry 1838 (`MosClient_Career.py`) uses the other-player namecard
operation for another UID. Its own-player branch instead reads synchronized
`PlayerAvatar.RankGame.ranks`; entry 4672 (`rank_utils.py`),
`get_opened_season_ranks:444`, accesses that property. Its bytecode gets
`getattr(gi.PlayerAvatar.RankGame, 'ranks', {})` at 86-138, returns a truthy
mapping at 140-146, and otherwise returns `{}` at 148-150. It applies no extra
opened-season or date filter. The normal `RankGame.C_OPEN_PANEL(1)` path supplies
current-season panel data, not a replacement for all-season history. Do not
route the owner through the other-player operation to fill this gap.

`game_social_token_probe.read_social_token` consumes initial entity records
before calling `on_game_connection`. The new observer hook receives these
validated envelopes before they are discarded, and continues through the
rank-panel reader. A reader attached only to the later game callback would
still miss the initial state.

## Owner identity and rank-component names

- Entry 3781, `PlayerAvatar`, sets `PROPOBJ_NAME = 'GameServiceAvatar'`
  at class bytecode 12-14.
- Entry 5041, `avatar_prop.py`, declares the first `BasicProp` field as
  `PField(P_INT, pk=True, sync=OWN_CLIENT)` at 48-62. Its key tuple at 242
  starts with `uid`. It separately declares `zone_id` and the `RankGameProp`
  keys `ranks` and `open_games`.
- Entry 6742, `on_avatar_create`, separates `GameServiceAvatar` from generic
  entities at 54-112 and assigns the owner avatar to `self.PlayerAvatar`
  at 476-480. `on_avatar_init` reads `avt.uid` at 64-76, assigns the game
  instance UID at 92-124 and supplies it to `set_own_player_uid` at 636-686.
- Entry 8865, `Entity.__init__`, assigns the engine `eid` separately.
  **An entity ID is not a player UID.** The synchronized UID must match the
  already-authenticated login owner before any owner-stat projection.
- Entry 5439, `RankGameComponent`, sets `VAR_NAME = 'RankGame'` at 12-14
  and applies `PropertyExtend(RankGameProp)` at 18-26.
- Entry 8866, `PropertyExtend:223`, uses the caller class name when no explicit
  `PROPOBJ_NAME` exists (180-192, 220-226). The rank descriptor class is thus
  `RankGameComponent`, not `GameServiceAvatar_RankGame`. The latter string is
  an explicit-component-index lookup key in `property_define`, not its
  serialized subobject class name.
- Entry 8858, `_components:94`, reads `component.VAR_NAME` at 460-472 and
  calls `__add_component` at 532-554. `__add_component:59` writes
  `klass._components_[name] = component` at 204-220. This proves the Python
  attachment name `RankGame`; the remaining bridge is established below.

### Component construction precedes native property initialization

The decorator does not itself call `Property(P_OBJ)` or
`mosprop.attach_obj_subdesc`. Instead, the current construction path creates an
existing component instance under its literal Python attribute before native
initialization reads the root extension:

- Entry 5440 imports `RankGameComponent` at 208-216 and includes it in
  `PLAYER_COMPONENTS` at 982-984/1222-1226.
- Entry 3781 constructs `components(PLAYER_COMPONENTS)` at 262-282 and applies
  the decorator at 318-332.
- Entry 8865, `Entity.__init__`, calls `_create_components` at 202-230.
  `_create_components` constructs `component(self)` at 30-50, then calls
  `setattr(self, VAR_NAME, instance)` at 52-84. `RankGame` therefore exists as
  a `RankGameComponent` instance.
- Entry 8864 creates `classType(initData)` at 238-258 before calling `do_init`
  at 306-336. Entry 8865, `do_init`, calls `init_prop_from_message` at 132-174.
- In native initialization, `0x559e553-0x559e568` calls the generic attribute
  helper at `0x9320960` for the literal incoming name. The GameObject
  `tp_getattro` wrapper at `0x931f3a0` reaches that helper, which handles
  descriptors, the instance dictionary, the type and `AttributeError`. These
  addresses describe the observed path, not a guessed recovered symbol name.
- With existing `RankGame` selected, `0x559e691-0x559e6a8` calls `0x559cf40`.
  Its type-zero extension branch at `0x559d53f-0x559d553` recursively invokes
  `0x559d840` on that existing object.

This establishes the literal root `RankGame` attachment to nested descriptor
`RankGameComponent`; it does not establish any numeric property index or allow
searching for a rank-like string at an arbitrary location in a blob.

## Native initialization and property-key contract

The `init_prop_from_message` Python method-table entry at `0xf36fff0` points
to wrapper `0x55bd810`. Its reviewed call path is:

```text
0x55bd810 -> 0x559d5f0 -> 0x559d6b0 -> 0x559d840
```

`0x559d6b0` invokes the unpack helper `0x55bae20`, requires object-type 9 at
`0x559d73a-0x559d741`, and checks extension type zero at
`0x559d78b-0x559d795`. It is not an ordinary top-level JSON or dictionary.

`0x559d840` parses the extension payload after its type byte. Its first
unpacked item must be a class-name string (`0x559d917`). It then repeatedly
unpacks a field key followed by its value until the extension payload is
consumed (`0x559de00`, `0x559de8b`, `0x559e549`). A key may be a string or a
positive integer. Numeric keys are resolved through the class's registered
16-bit field descriptors (`0x559e0e3-0x559e159`); guessing their meaning from
value order is not valid.

The `PField.add_field_desc` binding at `0x557f680` stores the unsigned 16-bit
index at descriptor offset `+4` (`0x557fa8e-0x557fa93`) and reaches
`0x55ab300`. At `0x55ab448-0x55ab45b`, index zero skips numeric-map insertion
and proceeds to `0x55ab51c`; nonzero indices enter the 16-bit lookup map.
`PField`'s default `index=0` is therefore **not automatic positional numbering**.
No reviewed numeric RankGame/ranks mapping has been established. The candidate
decoder rejects numeric property keys rather than assigning guessed names.

The value converters at `0x55a06f0` and `0x559cf40` recursively handle arrays,
maps and extension objects. Type-zero nested extensions return to
`0x559d840`. The latter converter clears an existing map before applying a
replacement map (`0x559d1f9-0x559d217`); this alone does not establish the
separate incremental property-update protocol.

### Wire tags are verified separately from native in-memory enums

The unpack path is `0x55bae20 -> 0x55bafa0 -> 0x558c590 -> 0x55b4dc0`, with
the chained parser at `0x55b4e0c`. Jump table `0x55b57e0` establishes standard
MessagePack wire tags: fixints/fixstr/fixarray/fixmap, `C4-C6` binary,
`C7-C9` extension, `CA/CB` float, `CC-D3` integer, `D4-D8` fixed extension,
`D9-DB` string, `DC/DD` array and `DE/DF` map.

The `C7-C9` wire length excludes the extension type byte. The parser adds one
byte when consuming it; `0x55bb320` stores length minus one at offsets
`0x3aa-0x3b7`. The extension payload passed to property decoding begins with
the class string, followed by streamed key/value pairs, not an enclosing map.
The [MessagePack format specification](https://github.com/msgpack/msgpack/blob/master/spec.md)
agrees with these traced wire tags and extension lengths. The native trace,
not a modern library's internal enum, establishes their use in this build.

Do not copy modern MessagePack C++ in-memory enum numbers into the decoder.
The actual jump tables at `0x55a0c18` and `0x559d5c4` route internal type 6
to arrays, 7 to maps, 8 to an unsupported branch and 9 to extensions. The
[upstream current enum](https://raw.githubusercontent.com/msgpack/msgpack-c/cpp_master/include/msgpack/v1/object_fwd_decl.hpp)
differs for arrays/maps/binary. Its documentation is corroboration for the
general MessagePack model, not proof of this game's exact implementation.

## Supported candidate rank-row representation

`RankGameProp.ranks` is declared `P_MAP`. Current entry 5535, `get_rank_key`,
computes `login_os * 1000000 + rank_game_type * 1000 + rank_game_id` without
string conversion, so integral inputs produce integer keys. Entry 5439,
`n_rank_level`, looks up `self.ranks.get(get_rank_key(...), {})` at 160-292,
then reads `rank_data.get('level', 0)` at 294-336. Entry 4672,
`get_max_rank_level_in_ranks`, iterates items and uses
`rank_info.get('max_level', 0)`.

Native `P_MAP` conversion at `0x55a0870` recursively handles ordinary
MessagePack map pairs. These facts support an **integer-key/plain-row-map
candidate**, but do not prove the actual live serialized representation:
extension row objects are possible in the native converter and their class
contract has not been established. The implementation accepts only integer
keys and plain selected row maps; it neither converts string keys nor guesses
an extension-row classname from the separate namecard JSON route.

The projection selects PC OS `1`, normal rank type `1`, and seasons `1..999`.
Each selected row must contain a `rank_game_id` consistent with its key plus
bounded `level`, `rank_score`, `max_level` and `max_rank_score` values.
Presence of `rank_game_id` in native delivery is not yet proven; missing fields
produce a fixed unavailable diagnostic rather than fabricated defaults.
Optional match/win counts, timestamps and placements remain unknown if invalid.
Missing `ranks` is unavailable; an explicitly returned empty map can represent
an available empty history. Neither implies an "unranked" label.

No current season is inferred from the greatest key, local date or
`open_games`; current panel data remains separate. Per-season numeric peaks
are not a normalized overall peak. Table-derived labels and full history
completeness are not claimed.

## Implemented candidate and integration

- `game_property_codec.py` implements bounded `decode_named_fields` for a
  complete type-zero extension or an explicitly selected opaque extension.
  It retains only requested literal fields, rejects unsupported numeric
  property keys, enforces framing/duplicate/depth/size/node bounds, and leaves
  nested extensions opaque until their expected class is explicitly supplied.
  It performs no native sentinel conversion, class instantiation or code
  execution. Private return objects have redacted representations.
- `owner_rank_snapshot.py` requests exact root class `GameServiceAvatar` and
  fields `uid`, `zone_id`, `RankGame`, then exact nested class
  `RankGameComponent` and field `ranks`. Root UID must equal the configured
  owner; its positive zone must match the caller's verified session zone.
  It emits only fixed metadata and bounded projected season rows, never raw
  initialization data, credentials or fabricated AUTH/request-bound flags.
- `game_career_probe.StartupEvent` and optional `on_startup` hooks deliver
  validated create/destroy/property/reset envelopes before consumption.
  Current main 65534 creation/sync descriptors are in entry 5115
  (`sys_pto.py`), not avatar-RPC entry 5113. ACKs remain informational; reset
  still raises the existing protocol-handling error after observer invalidation.
- `game_social_token_probe` observes before the initial token callback, and
  `game_rank_panel_probe` passes later lifecycle events to the same observer.
  `standalone_profile_bundle` creates it only for the owner target and projects
  the result after the startup/panel window, with owner login, identity, zone,
  completed continuation, verified panel and reusable-connection gates.
  Existing panel current rank and other-player routes are unchanged.
- Active-entity destruction, unsupported property updates and resets clear
  the rank snapshot. Same-EID recreation clears the previous generation before
  decoding; multiple live owner entities are ambiguous. A later full,
  unambiguous create can recover ordinary failures, but hard observer bounds
  remain terminal. Observer failures affect only owner history, not otherwise
  verified identity, Career or matches.
- Synthetic codec, observer and hook tests cover named/numeric fields,
  nested extensions, identity/zone and season binding, malformed framing,
  duplicates, bounds, lifecycle invalidation, redaction and failure isolation.
  These tests are not evidence that a live server sends the supported shape.

The explicit server-package allowlist now contains 27 runtime modules and
32 files, including the candidate codec and observer. This is source allowlist
metadata, not confirmation of a newly built ZIP, archive hash or deployment.

## Remaining acceptance

1. Observe a normal authorized owner connection and establish whether live
   startup uses supported named properties, exact classes, integer rank keys
   and plain rows with the required fields. Unsupported numeric keys or
   extension rows need their own source-backed contract before any expansion.
2. Verify the narrow owner-history projection and its PC/normal-season
   selection in the full profile bundle. Check fixed diagnostics and preserve
   working sections if history remains unavailable or an incremental update
   invalidates it. Do not retain or publish raw initialization data.
3. Complete regression and isolated package checks for the integrated candidate,
   rebuild any distributable explicitly, and verify the actual web/HTTP result.
   Static evidence and synthetic acceptance alone are not hosted acceptance.
4. Keep hosting selection, authorized persistent server bootstrap and hosted
   restart/renewal verification separate. No laptop-independent deployment or
   long-term credential-renewal success is established by this decoder work.

## Live integration checkpoint

Two ephemeral normal saved-Steam SDK exchanges and owner gateway/profile
reads completed from the authorized official-store session. Exact owner
1370598422 AUTH, mesh identity, Career, current panel and first-page matches
remained verified. The second preserved only fixed initialization diagnostics:
`initialization_outcome=owner_rank_snapshot_projected`, followed by final
`owner_rank_property_update_unhandled`. This proves initial named property
decoding/projection succeeded on that connection, not that final history is
available. An empty map also passes projection; initial row count/season rows
were not retained for this diagnostic, so nonempty live history is not yet
established. No raw init/sync or credentials were saved.

Next is the source-backed incremental property-sync path. Only a proven
unrelated named change may leave the snapshot intact; rank, identity and
unknown updates must invalidate it until their semantics are implemented.
Final live season selection and hosted acceptance remain required.
