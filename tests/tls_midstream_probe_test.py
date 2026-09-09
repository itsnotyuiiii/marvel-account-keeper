"""Published RFC 8448 vectors and synthetic fixtures; no real session material."""
import io
import hashlib
import json
import unittest
from unittest.mock import patch

import tls_midstream_probe as tls


# Public RFC 8448 section 3 application secrets/records, not user credentials.
# The server record has sequence 1 because its NewSessionTicket used sequence 0.
CLIENT = bytes.fromhex("9e40646ce79a7f9dc05af8889bce6552875afa0b06df0087f792ebb7c17504a5")
SERVER = bytes.fromhex("a11af9f05531f856ad47116b45a950328204b4f44bfb6b3a4b4f1f3fcb631643")
CLIENT_RECORD = bytes.fromhex("1703030043a23f7054b62c94d0affafe8228ba55cbefacea42f914aa66bcab3f2b9819a8a5b46b395bd54a9a20441e2b62974e1f5a6292a2977014bd1e3deae63aeebb21694915e4")
SERVER_RECORD = bytes.fromhex("17030300432e937e11ef4ac740e538ad36005fc4a46932fc3225d05f82aa1b36e30efaf97d90e6dffc602dcb501a59a8fcc49c4bf2e5f0a21c0047c2abf332540dd032e167c2955d")


def keylog(client=CLIENT, server=SERVER, random=b"r" * 32):
    return b"".join(f"{label} {random.hex()} {secret.hex()}\n".encode()
                    for label, secret in zip(tls.APP_LABELS, (client, server)))


def encrypt_record(secret, sequence, body=b"synthetic", cipher=tls.CIPHERS[0], kind=23, padding=0):
    aead, iv = tls.record_keys(secret, cipher)
    plain = body + bytes([kind]) + b"\0" * padding
    header = tls.PREFIX + (len(plain) + 16).to_bytes(2, "big")
    return header + aead.encrypt(tls.nonce(iv, sequence), plain, header)


class TlsMidstreamProbeTest(unittest.TestCase):
    def analyze(self, client=CLIENT_RECORD, server=SERVER_RECORD, keys=None, limit=8):
        return tls.analyze(client, server, io.BytesIO(keylog() if keys is None else keys), limit)

    def test_published_key_and_iv_expansion(self):
        self.assertEqual(tls.expand_label(SERVER, b"key", 16).hex(), "9f02283b6c9c07efc26bb9f2ac92e356")
        self.assertEqual(tls.expand_label(SERVER, b"iv", 12).hex(), "cf782b88dd83549aadf1e984")
        self.assertEqual(tls.expand_label(CLIENT, b"key", 16).hex(), "17422dda596ed5d9acd890e3c63f5051")
        self.assertEqual(tls.expand_label(CLIENT, b"iv", 12).hex(), "5b78923dee08579033e523d9")

    def test_published_bidirectional_application_records_without_hellos(self):
        report = self.analyze()
        self.assertTrue(report["record_decryption_verified"])
        self.assertEqual(report["client"]["starting_sequence"], 0)
        self.assertEqual(report["server"]["starting_sequence"], 1)
        self.assertEqual(report["client"]["application_bytes_authenticated"], 50)
        self.assertEqual(report["server"]["application_bytes_authenticated"], 50)
        self.assertFalse(report["tls_peer_identity_verified"])
        self.assertFalse(report["career_access_verified"])
        self.assertFalse(report["full_connection_decrypted"])

    def test_record_nonce_uses_network_order_and_rejects_invalid_inputs(self):
        self.assertEqual(tls.nonce(bytes(12), 0x102), bytes(10) + b"\x01\x02")
        self.assertEqual(tls.nonce(bytes(range(12)), 0x102), bytes(range(10)) + bytes([11, 9]))
        for iv, sequence in ((bytes(8), 0), (bytes(12), -1), (bytes(12), 2 ** 64)):
            with self.assertRaises(tls.EvidenceError):
                tls.nonce(iv, sequence)

    def test_public_record_plaintext_matches_independent_vector(self):
        keys = tls.record_keys(CLIENT, tls.CIPHERS[0])
        matches = tls.match_direction(CLIENT_RECORD, {(0, tls.CIPHERS[0]): keys}, 1, tls.Budget())
        decoded = matches[(0, tls.CIPHERS[0])]
        self.assertEqual(decoded.application, bytes(range(50)))
        self.assertNotIn(bytes(range(50)).hex(), repr(decoded))

    def test_other_supported_ciphers_and_padded_records(self):
        for cipher in tls.CIPHERS:
            with self.subTest(cipher=cipher):
                size = 48 if cipher == tls.CIPHERS[1] else 32
                client, server = b"c" * size, b"s" * size
                report = self.analyze(encrypt_record(client, 4, cipher=cipher, padding=20),
                                      encrypt_record(server, 7, cipher=cipher), keylog(client, server))
                self.assertTrue(report["record_decryption_verified"])
                self.assertEqual(report["record_cipher"], cipher)

    def test_partial_leading_and_trailing_records_remain_explicit(self):
        report = self.analyze(b"partial-ciphertext" + CLIENT_RECORD + CLIENT_RECORD[:9])
        self.assertTrue(report["record_decryption_verified"])
        self.assertEqual(report["client"]["prefix_bytes_unexamined"], 18)
        self.assertTrue(report["client"]["truncated_tail"])
        self.assertFalse(report["full_connection_decrypted"])

    def test_wrong_keys_or_counter_range_do_not_match(self):
        self.assertFalse(self.analyze(keys=keylog(b"x" * 32, b"y" * 32))["record_decryption_verified"])
        self.assertFalse(self.analyze(limit=0)["record_decryption_verified"])

    def test_changed_tag_and_authenticated_chain_gap_are_rejected(self):
        tampered = SERVER_RECORD[:-1] + bytes([SERVER_RECORD[-1] ^ 1])
        self.assertFalse(self.analyze(server=tampered)["record_decryption_verified"])
        # A missing record, or duplicate TLS ciphertext, cannot be silently skipped.
        for following in (CLIENT_RECORD, encrypt_record(CLIENT, 2)):
            report = self.analyze(client=CLIENT_RECORD + following)
            self.assertEqual(report["outcome"], "authenticated_anchor_with_unverified_tail")
            self.assertFalse(report["record_decryption_verified"])

    def test_separate_sessions_cannot_pair_opposite_directions(self):
        keys = keylog(CLIENT, b"x" * 32) + keylog(b"y" * 32, SERVER, b"z" * 32)
        report = self.analyze(keys=keys)
        self.assertEqual(report["outcome"], "unpaired_direction_match")
        self.assertFalse(report["bidirectional_session_match"])

    def test_duplicate_alias_pairs_are_ambiguous(self):
        report = self.analyze(keys=keylog() + keylog(random=b"z" * 32))
        self.assertEqual(report["outcome"], "ambiguous_keylog_session_match")
        self.assertFalse(report["record_decryption_verified"])

    def test_hours_long_keylog_and_recent_session_at_default_counter_limit(self):
        # Match the observed 218-session size using wholly synthetic unrelated
        # keys followed by the independent public RFC vector, never user keys.
        unrelated = b"".join(
            keylog(hashlib.sha256(f"client-{i}".encode()).digest(),
                   hashlib.sha256(f"server-{i}".encode()).digest(), i.to_bytes(32, "big"))
            for i in range(217)
        )
        report = self.analyze(keys=unrelated + keylog(), limit=tls.MAX_START_SEQUENCE)
        self.assertTrue(report["record_decryption_verified"])
        self.assertEqual(report["complete_application_key_pairs"], 218)
        self.assertEqual(report["keylog_pair_index_for_this_read"], 217)
        self.assertEqual(report["client"]["starting_sequence_search_ceiling"], 0)
        self.assertEqual(report["server"]["starting_sequence_search_ceiling"], 1)
        self.assertEqual(report["decryption_attempts"], 218 * 2 * 3)
        self.assertFalse(report["career_access_verified"])

    def test_search_preserves_old_keys_and_high_counters(self):
        # Older long-lived game flows must not lose their keys to a latest-N
        # truncation. A counter above the fast/common starting range still works.
        keys = keylog() + keylog(b"x" * 32, b"y" * 32, b"z" * 32)
        report = self.analyze(encrypt_record(CLIENT, 257), encrypt_record(SERVER, 258), keys, 258)
        self.assertTrue(report["record_decryption_verified"])
        self.assertEqual(report["keylog_pair_index_for_this_read"], 0)
        self.assertEqual(report["client"]["starting_sequence_search_ceiling"], 257)
        self.assertEqual(report["server"]["starting_sequence_search_ceiling"], 258)

    def test_duplicate_lines_and_conflicts_use_existing_validation(self):
        self.assertTrue(self.analyze(keys=keylog() * 2)["record_decryption_verified"])
        report = self.analyze(keys=keylog() + keylog(client=b"x" * 32) + keylog())
        self.assertFalse(report["record_decryption_verified"])

    def test_no_key_read_without_candidate_records(self):
        source = io.BytesIO(keylog())
        report = tls.analyze(b"not TLS", b"", source)
        self.assertEqual(report["outcome"], "no_complete_tls13_candidate_records")
        self.assertEqual(source.tell(), 0)

    def test_invalid_authenticated_inner_record_is_not_success(self):
        report = self.analyze(encrypt_record(CLIENT, 0, kind=20))
        self.assertFalse(report["record_decryption_verified"])
        self.assertEqual(report["client"]["chain_error"], "authenticated_inner_content_type_invalid")

    def test_counts_and_fixed_markers_never_export_plaintext_or_keys(self):
        private = b"POST /xyq_service/xyq-career/query_my_player_career HTTP/1.1\r\nAuthorization: synthetic-private-token\r\n\r\n"
        report = self.analyze(encrypt_record(CLIENT, 0, private))
        self.assertEqual(report["client"]["known_career_operation_markers"], ["query_my_player_career"])
        serialized = json.dumps(report)
        for value in ("synthetic-private-token", "Authorization", CLIENT.hex(), SERVER.hex(), (b"r" * 32).hex()):
            self.assertNotIn(value, serialized)
        self.assertFalse(report["application_protocol_decoded"])
        self.assertFalse(report["career_access_verified"])

    def test_search_and_resource_limits(self):
        self.assertEqual(self.analyze(limit=tls.MAX_START_SEQUENCE + 1)["outcome"], "sequence_search_limit_invalid")
        with patch.object(tls, "MAX_ATTEMPTS", 0):
            self.assertEqual(self.analyze()["outcome"], "decryption_attempt_limit_exceeded")
        with patch.object(tls, "MAX_SECONDS", -1):
            self.assertEqual(self.analyze()["outcome"], "decryption_time_limit_exceeded")
        with patch.object(tls, "MAX_SESSIONS", 0):
            self.assertEqual(self.analyze()["outcome"], "keylog_session_limit_exceeded")
        with patch.object(tls, "MAX_RECORDS", 0):
            self.assertEqual(self.analyze()["outcome"], "record_count_limit_exceeded")
        with patch.object(tls, "MAX_APPLICATION_BYTES", 2):
            self.assertEqual(self.analyze()["outcome"], "application_byte_limit_exceeded")
        with patch.object(tls.capture, "MAX_FLOW_BYTES", 1):
            self.assertEqual(self.analyze()["outcome"], "flow_limit_exceeded")

    def test_metadata_default_never_loads_capture_or_keys(self):
        with patch.object(tls.capture, "load_selected_flow") as read, patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(tls.main(["synthetic.pcapng"]), 0)
            read.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())["outcome"], "metadata_only")


if __name__ == "__main__":
    unittest.main()
