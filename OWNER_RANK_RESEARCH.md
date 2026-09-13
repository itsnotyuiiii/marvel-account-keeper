# Owner season-history acquisition: current source evidence

Read-only checkpoint, September 12, 2026 (local time). This is a decoding
contract investigation, not a live owner-history result or a deployed feature.
The current server still marks the owner's season-history section unavailable.

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
  No account records, credentials, captures or live connections were inspected.

Offsets below are bytecode offsets within the named function, or native RVAs
when prefixed `0x`. They are specific to the pinned distribution.

## Why the owner needs a different reader

Current entry 1838 (`MosClient_Career.py`) uses the other-player namecard
operation for another UID. Its own-player branch instead reads synchronized
`PlayerAvatar.RankGame.ranks`; entry 4672 (`rank_utils.py`),
`get_opened_season_ranks:444`, accesses that property. `C_MY_SEASON(1)` supplies
current-season panel data, not a verified replacement for all-season history.
Do not route the owner through the other-player operation to fill this gap.

`game_social_token_probe.read_social_token` currently consumes the initial
entity records before calling `on_game_connection`. Adding a reader only to
that later callback cannot recover already-consumed initialization bytes.

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
  attachment name `RankGame`, not the native root serialization bridge.

That decorator does not call `Property(P_OBJ)` or `mosprop.attach_obj_subdesc`.
Both Component and Entity derive from native `MosGamePlay.GameObject`.
The bridge from `_components_` to root serialized properties and numeric
field-index resolution remains to be checked. `PField` defaults `index=0`;
no explicit RankGame/ranks index was found in these Python definitions.
A string resembling a rank field elsewhere in a blob is not sufficient proof.

## Native initialization decoder located

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

The value converters at `0x55a06f0` and `0x559cf40` recursively handle arrays,
maps and extension objects. Type-zero nested extensions return to
`0x559d840`. The latter converter clears an existing map before applying a
replacement map (`0x559d1f9-0x559d217`); this alone does not establish the
separate incremental property-update protocol.

Do not copy modern MessagePack C++ in-memory enum numbers into this decoder.
The actual jump tables at `0x55a0c18` and `0x559d5c4` route internal type 6
to arrays, 7 to maps, 8 to an unsupported branch and 9 to extensions. The
[upstream current enum](https://raw.githubusercontent.com/msgpack/msgpack-c/cpp_master/include/msgpack/v1/object_fwd_decl.hpp)
differs for arrays/maps/binary. Its documentation is corroboration for the
general MessagePack model, not proof of this game's exact implementation.
An on-disk byte-level fixture and the actual wire tags are still needed.

## Remaining implementation and acceptance

1. Finish root-component attachment, field-index and wire-tag validation.
2. Implement a bounded passive decoder retaining only exact-owner UID/zone and
   reviewed rank fields. Skip unrelated properties and never instantiate
   classes named by remote data or execute remote code.
3. Observe startup before the token callback. Account for owner entity
   destruction/reset and property updates; do not keep a stale snapshot after
   an unsupported update. Main 65534 creation/sync descriptors are in current
   entry 5115 (`sys_pto.py`), not the avatar-RPC protocol entry 5113.
4. Add synthetic framing, identity, truncation, duplicate-key, depth/size and
   lifecycle tests; keep unsupported formats explicitly unavailable.
5. Live-verify the narrow projection on an authorized owner connection, compare
   its season selection with the reviewed PC/normal-rank keys, and integrate
   only the verified section. Keep current panel rank distinct from history.

No new live NetEase call, owner snapshot, runtime decoder or frontend fallback
was introduced by this checkpoint. Independent hosting, authorized server
bootstrap and hosted restart/renewal acceptance remain separate requirements.
