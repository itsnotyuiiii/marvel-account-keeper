"""Read a local NDIS ETL; audit headers before optional Career protocol hints.

Packet bytes pass only through a local child-process pipe, never to the console
or network. The supported frame layout is the one observed in this experiment.
This does not acquire credentials, decrypt, replay requests, or prove Career access.
"""
import argparse
from collections import Counter, defaultdict
import ipaddress
import json
import os
from pathlib import Path
import runpy
import struct
import subprocess
import base64


APPROVED_IPS = frozenset({"35.236.255.68", "8.221.212.39"})
PLAYER_LOOKUP_IPS = frozenset({"34.60.237.47", "35.236.255.68"})
MAX_TRACE_BYTES = 64 * 1024 * 1024

EVENT_READER = r"""
$ErrorActionPreference = 'Stop'
$allEvents = @(Get-WinEvent -Path $env:MAK_NETSH_ETL_PATH -Oldest -ErrorAction Stop)
$ndis = @($allEvents | Where-Object { $_.ProviderName -eq 'Microsoft-Windows-NDIS-PacketCapture' })
$packets = @($ndis | Where-Object Id -eq 1001 | ForEach-Object {
    $fields = $_.Properties
    if ($fields.Count -ne 6 -or $fields[3].Value -isnot [byte[]]) { throw 'Unexpected NDIS event schema.' }
    [pscustomobject]@{
        at_utc=$_.TimeCreated.ToUniversalTime().ToString('o')
        keywords=$_.Keywords.ToString()
        declared_bytes=[int]$fields[2].Value
        frame_base64=[Convert]::ToBase64String([byte[]]$fields[3].Value)
    }
})
[pscustomobject]@{
    total_events=$allEvents.Count
    packet_events=$packets
    unsupported_packet_events=@($ndis | Where-Object Id -eq 1003).Count
} | ConvertTo-Json -Depth 5 -Compress
"""


def read_events(path):
    path = Path(path).resolve(strict=True)
    if path.suffix.lower() != ".etl" or path.stat().st_size > MAX_TRACE_BYTES:
        raise ValueError("Expected a local ETL of at most 64 MiB.")
    child_env = dict(os.environ, MAK_NETSH_ETL_PATH=str(path))
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", EVENT_READER],
        env=child_env, capture_output=True, timeout=90,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        # Do not echo the native output: it might contain binary event values.
        raise ValueError("Local ETL reader failed; native output was not disclosed.")
    return json.loads(result.stdout.decode("utf-8-sig"))


def packet_header(event, *, approved_ips=APPROVED_IPS, allow_truncated=False):
    frame = base64.b64decode(event["frame_base64"], validate=True)
    keywords = int(event["keywords"]) & ((1 << 64) - 1)
    if event["declared_bytes"] != len(frame) or len(frame) > 65535:
        raise ValueError("NDIS fragment size mismatch")
    truncated = bool(keywords & 0x02000000)
    if keywords & 0xC0000000 != 0xC0000000 or truncated and not allow_truncated:
        raise ValueError("incomplete or truncated NDIS packet")
    if not keywords & 0x10000:
        raise ValueError("unreviewed link type")
    # Observed native 802.11 data frame, no QoS/4-address extension, then SNAP.
    if (len(frame) < 72 or frame[0] & 0xFC != 8 or frame[1] & 3 == 3
            or frame[24:32] != b"\xaa\xaa\x03\x00\x00\x00\x08\x00"):
        raise ValueError("unsupported wireless/IP header")
    raw = frame[32:]
    ihl = (raw[0] & 15) * 4
    declared_total = int.from_bytes(raw[2:4], "big")
    # NDIS can expose an outbound LSO packet before IPv4 total length is filled.
    # Preserve its captured TCP range, not an invented original/wire length.
    total = declared_total or len(raw)
    if (raw[0] >> 4 != 4 or ihl < 20 or total < ihl + 20 or
            total > len(raw) and not (truncated and allow_truncated)
            or raw[9] != 6 or int.from_bytes(raw[6:8], "big") & 0x3FFF):
        raise ValueError("unsupported or incomplete IPv4/TCP packet")
    source = str(ipaddress.IPv4Address(raw[12:16]))
    destination = str(ipaddress.IPv4Address(raw[16:20]))
    # A recorded prefix still has auditable IP/TCP headers. Never fill in its
    # missing bytes or merge across the resulting TCP gap. Opt-in research only;
    # the production capture/session decoder retains the strict default.
    tcp = raw[ihl:min(total, len(raw))]
    if len(tcp) < 20:
        raise ValueError("incomplete TCP header")
    source_port, destination_port, seq, ack = struct.unpack_from("!HHII", tcp)
    tcp_header_size = (tcp[12] >> 4) * 4
    if tcp_header_size < 20 or tcp_header_size > len(tcp):
        raise ValueError("incomplete TCP header")
    if (source in approved_ips) == (destination in approved_ips):
        raise ValueError("packet outside the unambiguous approved peer scope")
    outbound = destination in approved_ips
    key = ((destination, destination_port, source, source_port) if outbound else
           (source, source_port, destination, destination_port))
    return {"key": key, "direction": "out" if outbound else "in", "seq": seq,
            "ack": ack, "flags": tcp[13], "payload": tcp[tcp_header_size:],
            "at_utc": event["at_utc"], "ip_length_from_capture": declared_total == 0,
            "truncated": truncated, "missing_payload_bytes": max(0, total - len(raw))}


def analyze_events(events, protocol_hints=False, *, approved_ips=APPROVED_IPS, allow_truncated=False):
    if approved_ips not in (APPROVED_IPS, PLAYER_LOOKUP_IPS):
        raise ValueError("Unreviewed capture scope")
    packets = []
    rejected = Counter()
    for event in events["packet_events"]:
        try:
            packets.append(packet_header(event, approved_ips=approved_ips, allow_truncated=allow_truncated))
        except (ValueError, KeyError, TypeError, struct.error):
            rejected["unparsed_or_out_of_scope"] += 1
    scope_ok = bool(packets) and not rejected and not events["unsupported_packet_events"]
    result = {"packet_event_count": len(events["packet_events"]),
              "parsed_packet_count": len(packets), "rejected": dict(rejected),
              "unsupported_packet_events": events["unsupported_packet_events"],
              "all_recorded_packet_headers_in_scope": scope_ok,
              "truncated_packet_count": sum(p["truncated"] for p in packets),
              "all_recorded_packets_complete": bool(scope_ok and not any(p["truncated"] for p in packets)),
              "protocol_hints_requested": protocol_hints,
              "protocol_hints_performed": bool(protocol_hints and scope_ok),
              "every_packet_process_ownership_verified": False,
              "career_access_verified": False, "network_requests_sent": False}
    groups = defaultdict(list)
    for packet in packets:
        groups[packet["key"]].append(packet)
    hint_function = None
    if protocol_hints and scope_ok:
        hint_function = runpy.run_path(str(Path(__file__).with_name("summarize-career-capture.py")))["stream_hints"]
    flows = []
    for key, group in sorted(groups.items()):
        syn = next((p for p in group if p["direction"] == "out" and p["flags"] & 0x12 == 2), None)
        synack = next((p for p in group if syn and p["direction"] == "in" and
                       p["flags"] & 0x12 == 0x12 and p["ack"] == (syn["seq"] + 1) % (1 << 32)), None)
        final_ack = any(p["direction"] == "out" and p["flags"] & 0x12 == 0x10 and
                        p["ack"] == (synack["seq"] + 1) % (1 << 32)
                        for p in group) if synack else False
        flow = {"remote_ip": key[0], "remote_port": key[1], "local_ip": key[2], "local_port": key[3],
                "packets": len(group), "first_at_utc": group[0]["at_utc"], "last_at_utc": group[-1]["at_utc"],
                "tcp_three_way_handshake_observed": final_ack,
                "truncated_packet_count": sum(p["truncated"] for p in group),
                "missing_payload_bytes": sum(p["missing_payload_bytes"] for p in group),
                "zero_ip_length_offload_packets": sum(p["ip_length_from_capture"] for p in group),
                "flags": dict(Counter(p["flags"] for p in group)), "streams": {}}
        for direction in ("out", "in"):
            chunks = [(p["seq"] + bool(p["flags"] & 2), p["payload"]) for p in group
                      if p["direction"] == direction and p["payload"]]
            flow["streams"][direction] = (hint_function(chunks) if hint_function else
                                            {"captured_payload_bytes": sum(len(data) for _, data in chunks)})
        flows.append(flow)
    result["flows"] = flows
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", help="Local NDIS ETL path; never uploaded")
    parser.add_argument("--protocol-hints", action="store_true", help="Run existing bounded hints only if ALL packet headers pass scope checks")
    parser.add_argument("--player-lookup", action="store_true", help="Use the separately reviewed normal-player-lookup two-IP scope, not the older handshake scope")
    parser.add_argument("--inspect-truncated", action="store_true", help="Inspect scoped captured prefixes with complete IP/TCP headers; report truncation and never fill TCP gaps")
    args = parser.parse_args()
    try:
        scope = PLAYER_LOOKUP_IPS if args.player_lookup else APPROVED_IPS
        print(json.dumps(analyze_events(read_events(args.trace), args.protocol_hints, approved_ips=scope,
                                       allow_truncated=args.inspect_truncated), indent=2))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        print(json.dumps({"outcome": "local_capture_read_failed", "career_access_verified": False,
                          "network_requests_sent": False}))
        raise SystemExit(1)
