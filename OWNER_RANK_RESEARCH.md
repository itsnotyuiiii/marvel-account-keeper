# Owner season-history acquisition: current source evidence

Static-contract and live-integration checkpoint, September 12, 2026 (local
time). The bounded named-property decoder, incremental-sync path parser,
owner-rank observer and startup hooks are implemented. **The final live owner
profile bundle now verifies all 12 returned season rows alongside identity,
Career, current panel and first-page matches.** The Traceroute interruption was
resolved through a narrowly bounded native attribute-assignment proof, not a
guessed field name or wildcard exemption. This is not hosted acceptance or
proof of complete history. Unsupported input remains an unavailable section,
not evidence of an invalid player UID.

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

## Supported rank-row representation

`RankGameProp.ranks` is declared `P_MAP`. Current entry 5535, `get_rank_key`,
computes `login_os * 1000000 + rank_game_type * 1000 + rank_game_id` without
string conversion, so integral inputs produce integer keys. Entry 5439,
`n_rank_level`, looks up `self.ranks.get(get_rank_key(...), {})` at 160-292,
then reads `rank_data.get('level', 0)` at 294-336. Entry 4672,
`get_max_rank_level_in_ranks`, iterates items and uses
`rank_info.get('max_level', 0)`.

Native `P_MAP` conversion at `0x55a0870` recursively handles ordinary
MessagePack map pairs. These facts support an **integer-key/plain-row-map
representation**, which has now passed live initialization for 12 selected
owner season rows. This does not establish every possible delivery shape:
extension row objects are possible in the native converter and their class
contract has not been established. The implementation accepts only integer
keys and plain selected row maps; it neither converts string keys nor guesses
an extension-row classname from the separate namecard JSON route.

The projection selects PC OS `1`, normal rank type `1`, and seasons `1..999`.
Each selected row must contain a `rank_game_id` consistent with its key plus
bounded `level`, `rank_score`, `max_level` and `max_rank_score` values.
The successful 12-row initialization passed these required-field checks;
missing fields in another delivery still produce a fixed unavailable
diagnostic rather than fabricated defaults.
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
- `game_property_sync.py` implements bounded `decode_sync_paths`, accepting
  one complete array of type-two extensions containing reviewed operations
  1/2/3 and ordinary path/value entries. It preserves only paths, operations
  and wire value kinds, not values or raw payloads. The observer separately
  applies the exact unrelated-path classifier described below. It does not
  execute a native mutation engine or update rank rows from incremental data.
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
- Synthetic codec, sync-parser, observer and hook tests cover named/numeric fields,
  nested extensions, identity/zone and season binding, malformed framing,
  duplicates, bounds, no-ops, exact unrelated paths, alias/deeper-path rejection,
  mixed relevant/unrelated updates, lifecycle invalidation, redaction and
  failure isolation. These tests are separate from the live checkpoint below.

The explicit server-package allowlist now contains 28 runtime modules and
33 files, including the codec, sync parser and observer. This is source allowlist
metadata, not confirmation of a newly built ZIP, archive hash or deployment.

## Remaining acceptance

1. Complete isolated package verification and explicitly rebuild the selected
   distributable; the focused regression run passed 430 tests in 2.590 seconds.
   Package checking was still in progress at this checkpoint. Source allowlist
   counts and a live Python bundle do not establish a deployed package.
2. Verify the actual website/HTTPS response and owner-history presentation
   against the live backend. The successful ephemeral full-bundle result below
   is not hosted end-to-end acceptance. Preserve working sections when a future
   unsupported update invalidates history; do not save raw initialization or
   sync data. Numeric property keys and extension rows still require their own
   contracts before expanding support.
3. Complete hosting selection, authorized persistent server bootstrap and
   hosted restart/renewal verification separately. No laptop-independent
   deployment or long-term credential-renewal success is established by this
   decoder milestone. Rank labels, an overall peak and full-history
   completeness also remain unverified.

## Live integration checkpoint

Ephemeral normal saved-Steam SDK exchanges and owner gateway/profile reads
completed from the authorized official-store session. Exact owner 1370598422
AUTH, mesh identity, Career, current panel and first-page matches remained
verified. Earlier diagnostics established successful initial projection but
did not record a row count. Later bounded diagnostics established
`initialization_outcome=owner_rank_snapshot_projected` with
`initialization_season_count=12`; nonempty initial owner history is therefore
established for the observed connection. At that intermediate stage, final
`ranks_verified` was false with `owner_rank_property_update_unhandled` and an
unreviewed sync path; it was not yet a successful final history result.

All four previously unknown root fingerprints match exact literal `VAR_NAME`
declarations in the pinned public archive, not class-name suffix guesses:

| Literal root | Component entry | SHA256 of UTF-8 root |
| --- | --- | --- |
| `Traceroute` | 5410 | `5e472e4e9f3637790f5ddb9fc1cb702e5990d1e07f07621ca5f0af5a219c9f0e` |
| `Career` | 5371 | `732863fa5c6125a201487a2f52e400a2f29a8c9548512fc84c35da1aef3d710d` |
| `League` | 5416 | `7f06ea4f0831f4f9f155579f51dda6e70086bc1627ac2bea0d42f34bbcf3f8f3` |
| `Status` | 5386 | `920e413c7d411b61ef3e8c63b1cb6ad058d5f95f8b481dbafe60248387d8c355` |

The exact-field classifier accepted the observed Career, League and Status
updates. That left only the then-unreviewed `Traceroute` shape, path length two,
operation one, map value, with second-segment SHA256
`090772cf4068973daad3f715eb788d39fe2c02be42efd86de81f0e59198d6237`.
It differs from declared `rules`
(`6c621d1a05138a7888d37d9269a9da8e2e11e4aced2f6cfd24b05ab1b9e61bb0`).
The fingerprint matched no exact string/name, including nested constants, in
Traceroute 5410, Component 8859 or Property 8866. Traceroute has no constructor,
attribute writes or dynamic property-registration call. Its eight declarations
are `enable`, `rules`, `enable_traceroute`, `enable_sdk_traceroute`,
`enable_sdk_mtr`, `traceroute_count`, `sdk_traceroute_count`, `sdk_mtr_count`.

The field's name and business meaning remain unidentified. Native MRO and
data-descriptor review instead proved that this exact terminal write affects
only the separate Traceroute instance dictionary, not owner identity or ranks.
The implemented fingerprint-specific exemption is described below.

After that proof and its synthetic regressions, terminal helper session 74037
completed a fresh live full-bundle read with `outcome=profile_bundle_verified`.
Owner authentication, game login, exact-UID identity, Career, current season,
matches and final ranks were verified. The projected result was:

- UID `1370598422`, IGN `ゆいᶻᶻᶻ`, PC, account level 32.
- Final owner season rows: `20, 19, 17, 16, 15, 14, 13, 12, 5, 4, 3, 2`
  (12 rows), retained through the complete observed startup/panel window.
- Independently verified panel current season 20, numeric rank level 14 and
  raw server `rank_score` 4350; the greatest history key was not used as a
  substitute for the panel's authoritative current season.
- Career: 273 matches, 149 wins, 4,165 kills, 1,609 deaths and 3,145 assists.
- First match page: 50 matches. This does not establish all-match completeness.

Per-season numeric peak fields are not a verified overall peak. Rank labels
and full-history completeness remain unknown; the missing season numbers are
not interpreted as evidence of inactivity or missing-account errors. This
success establishes the observed owner full-bundle path, not universal player
coverage or hosted website acceptance.

Only bounded projected profile data and fixed diagnostics were retained; no
raw init/sync bytes or values, credentials or renewal secrets were saved. There
were no browser-storage, game or Wi-Fi changes. Hosting, persistent bootstrap,
site integration and hosted restart/renewal remain separate acceptance work.

## Incremental-sync entry, framing and implemented parser

Read-only follow-up on the same pinned native image located the exact receive
path, without retaining or decoding live sync bytes:

```text
65534 / 120 -> 0x55adfa0 -> 0x559a850 -> 0x55a46a0
            -> 0x55aba90 -> 0x55abe20
```

- `0x55a8de1` registers command `0xfffe78`; callback functor vtable
  `0xb618750`, invoke slot `+0x10`, points to `0x55adfa0`.
- That handler decodes the entity-ID/sync-blob envelope via `0x55aa670`.
  Manager `0x559a850` resolves the exact incoming entity ID at message offset
  `+0x10`; existing entities reach `0x55a46a0`, while unknown entities' updates
  are queued. This is still an engine entity ID, not an account UID.
- `0x55aba90` unpacks `sync_msg` and requires internal type6 (array) at
  `0x55abb58-0x55abb5d`. It iterates 24-byte native objects; extension type2
  is dispatched at `0x55abc91-0x55abca1` to `0x55abe20`.
- The type2 extension payload starts with an operation integer of the native
  positive-integer type, bounded to127 (`0x55abecd-0x55abf12`). It then reads
  repeated path/value entries through payload exhaustion, not a JSON map.
- Each entry starts with an integer header bounded toINT32_MAX
  (`0x55abf8f-0x55abff4`). Header bit `0x10` selects a distinct packed-index
  branch. The ordinary branch reads `header` path segments, each a string or
  signed/unsigned integer, followed by one value (`0x55ac340-0x55ac6cf`).
- Traversal at `0x55aaeb0` uses dictionary keys or existing object attributes
  (`0x55aaf85-0x55aafb2`), then recurses. Numeric object segments resolve
  registered field descriptors; their meaning cannot be inferred by order.
- Dictionary mutation helper `0x559fe90` sets keys for operations 1/3 and
  deletes for 2. Object helper `0x559ff80` instead converts the incoming value
  and calls generic `setattr` on the terminal object attribute for non-7
  operations, including 1/2/3 (`0x559ffc2-0x559ffcd`, through `0x931f3b0`).
  **Operation 2 is not universally a deletion.** Its separate operation-7
  branch remains unsupported by the parser/classifier.
- Every ordinary entry consumes a value, including operation 2
  (`0x55ac662-0x55ac6cf`). A valid operation-only extension is a no-op
  (`0x55abf1c -> 0x55acbcd -> 0x55acbec`); empty outer arrays are also accepted.

The implemented parser requires complete bounded array/ext2/operation/path
framing and shared byte/node/depth budgets. It rejects packed-index paths,
unknown operations, invalid/empty path segments and malformed/trailing data;
numeric segments are retained as numbers, never resolved by guessed indices.
The observer rejects unreviewed numeric roots. Wire `value_kind` describes a
MessagePack tag, not a native sentinel conversion or independently proven
source type. No update values or raw payloads escape the parser.

The native outer helper is weaker about trailing bytes and an extension
handler's failure, and can skip other extension types. The standalone parser
deliberately requires whole-message validity: a later failed entry invalidates
the entire result, never yielding a partial path list or treating failed
decoding as a nil value. This stricter acceptance is intentional, not a claim
that the native receiver enforces identical framing.

### Exact unrelated-path classifier and alias limits

The observer does not replay mutations. It retains the initial snapshot only
when every parsed change belongs to the following reviewed unrelated shapes;
rank/identity changes and unreviewed paths invalidate it. A later unrelated
update or no-op cannot restore invalidated history.

- Exactly one scalar root segment: `name` with a string, or `score`, `level`,
  `exp`, `create_time` with an integer. These are the five selected BasicProp
  declarations in entry 5041, not all root fields or arbitrary deeper paths.
- Exactly two segments, matching a declared field and the wire kind below:

| Component | Exact field(s) | Required value kind |
| --- | --- | --- |
| `Traceroute` | `rules` | map |
| `Career` | `show_shape_id`, `level_reward_record`, `frequently_used_hero_info`, `my_device_display_username_info`, `cur_multi_people_lobby_hero`, `hero_title_dict` | map |
| `League` | `league_regions` | array |
| `League` | `view_league_id`, `championship_logo`, `watched_MRC_video_season`, `league_ban_info` | string |
| `Status` | `state_infos` | map |

These field declarations are in component entries 5410, 5371, 5416 and 5386
respectively. For operations 1/2/3, the exact depth-two terminal object-attribute
branch replaces only that field with the newly converted value; it does not
traverse or mutate an alias of the previous field container. Native map and
array conversion allocate new `PyDict`/`PyList` containers at `0x55a0882` and
`0x55a0823`, with recursive element conversion at `0x55a0849`.

The sole deeper exemption is exactly
`("Status", "state_infos", direct_key)`: operations 1/3 require a map value;
operation 2 requires a null wire value. The final string/integer segment is a
dictionary key, not an object attribute or a traversal into a stored value.
Entry 5386 declares `Property('state_infos', P_MAP)` at 30-40 and has no
constructor or assignment alias for it. Base Component initialization only
sets `entity`; native GameObject construction creates separate object/descriptor
state without shared field values. Missing initial map attributes use fresh
map conversion and then `setattr` (`0x559e6e2`), so `state_infos` starts as its
own dictionary. This matters because the existing-map initialization branch
does clear/reuse the old dict (`0x559d20f`); a generic P_MAP declaration alone
would not prove independence.

There is **no whole-component prefix rule**. Entry 8859 `Component.__init__:33`
stores a weak reference to the owner in `self.entity`; a path through that
back-reference could reach `uid`, `zone_id` or `RankGame`. Component replacement,
arbitrary suffixes, `__dict__`, deeper map-value traversal and wrong wire kinds
remain unreviewed. Apart from the single structural case below, unknown fields
still invalidate the snapshot. Root labels used only for diagnostics do not
grant an exemption.

### Fingerprint-specific Traceroute terminal-map exemption

The additional implemented case requires all of the following: exact root
`Traceroute`, exactly two path segments, a string terminal field with SHA256
`090772cf4068973daad3f715eb788d39fe2c02be42efd86de81f0e59198d6237`, operation
1, and a map wire value. The field must not be `entity` or start with `__`.
Operations 2/3, deeper paths, another root or fingerprint, non-map values and
extension values remain rejected. The hash identifies only the exact observed
field; it does not assign a guessed public name, content schema or meaning.

The reviewed inheritance chain is
`TracerouteComponent -> Component -> GameObject -> object`. No data descriptor
in that MRO matches the field fingerprint. The terminal assignment path
`0x559ff80 -> 0x931f3b0 -> 0x9320c50` therefore writes to the separate
Traceroute instance dictionary after fresh map conversion at `0x55a0882`,
rather than invoking a matching descriptor setter or traversing an existing
map/back-reference. This proves the location and independence of this bounded
write without identifying the auxiliary field's business meaning. It does
not permit arbitrary Traceroute fields or component prefixes.

Exact native anchors: MRO lookup `0x93a3bb0` is called at `0x9320cce`;
the descriptor's `tp_descr_set` is checked at `0x9320ce2-0x9320cf7`.
Absent a matching data descriptor, the fallback targets only the instance's
managed dictionary at `0x9320d24` or ordinary dictionary slot at `0x9320d86`.
The Python Traceroute/Component classes define no matching descriptor or
`__setattr__`. Native GameObject type `0xf36fb20` has no members and its
getset table `0xf370070` contains only `eid`, whose hash differs. Type readiness
`0x93a9975-0x93a9995` supplies the base `object` type `0xf694420`; its empty
member table and sole dunder getset also cannot match. Thus this finite chain
does not route the exact field through a property registry or another object.

Synthetic observer tests cover the matching fingerprint case, wrong root/hash,
operation, depth and value kind, and explicit `entity`/dunder exclusions. The
successful final live bundle above verifies that this narrow classifier keeps
the 12 observed owner rows through the actual update window.
