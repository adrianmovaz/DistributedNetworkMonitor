"""Shared local protocol services for integration tests."""

import errno
import socket
import socketserver
import struct
import threading
import time
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser

import dns.flags
import dns.message
import dns.rcode
import dns.rdatatype
import dns.rrset
import pytest
from pyasn1.codec.ber import decoder, encoder
from pysnmp.proto import rfc1902, rfc1905
from pysnmp.proto.api import v2c

from network_monitor.config import DNSSettings, FTPSettings, SMTPSettings, SNMPSettings
from network_monitor.protocols.snmp import INTERFACE_ROOT, SYSTEM_OIDS


@pytest.fixture
def smtp_server():
    state = {
        "mode": "normal",
        "messages": [],
        "commands": [],
        "data_received": threading.Event(),
        "release": threading.Event(),
    }

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(3)

            def reply(line):
                self.wfile.write(line.encode("ascii") + b"\r\n")
                self.wfile.flush()

            if state["mode"] == "drop_greeting":
                state["release"].wait(timeout=1)
                return
            reply("220 monitor.test SMTP")
            try:
                while command := self.rfile.readline():
                    text = command.decode("ascii").strip()
                    state["commands"].append(text)
                    verb = text.split(" ", 1)[0].upper()
                    if verb == "EHLO":
                        reply("250-monitor.test")
                        reply("250-8BITMIME")
                        reply("250 SIZE 1000000")
                    elif verb in {"HELO", "MAIL", "RSET", "NOOP"}:
                        reply("250 OK")
                    elif verb == "RCPT":
                        if state["mode"] == "temp_rcpt":
                            reply("450 Try later")
                        elif state["mode"] == "perm_rcpt":
                            reply("550 No such user")
                        else:
                            reply("250 OK")
                    elif verb == "DATA":
                        reply("354 Send data")
                        lines = []
                        while line := self.rfile.readline():
                            if line == b".\r\n":
                                break
                            lines.append(line[1:] if line.startswith(b"..") else line)
                        state["data_received"].set()
                        if state["mode"] == "wait_data":
                            assert state["release"].wait(timeout=3)
                        if state["mode"] == "temp_data":
                            reply("451 Temporary error")
                        elif state["mode"] == "perm_data":
                            reply("554 Message rejected")
                        elif state["mode"] == "disconnect_data":
                            return
                        else:
                            state["messages"].append(
                                BytesParser(policy=policy.default).parsebytes(b"".join(lines))
                            )
                            reply("250 Accepted")
                            if state["mode"] == "quit_disconnect":
                                return
                    elif verb == "QUIT":
                        reply("221 Bye")
                        return
                    else:
                        reply("500 Unknown command")
            except (OSError, ConnectionError):
                return

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            yield SMTPSettings(port=server.server_address[1], timeout=1), state
        finally:
            state["release"].set()
            server.shutdown()
            thread.join(timeout=3)
            assert not thread.is_alive()


@pytest.fixture
def ftp_server():
    state = {
        "files": {"original.bin": b"\x00\xff\r\nFTP\x00bytes"},
        "mode": "normal",
        "commands": [],
        "release": threading.Event(),
    }

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(3)
            passive = None

            def reply(text):
                self.wfile.write(text.encode("utf-8") + b"\r\n")
                self.wfile.flush()

            try:
                if state["mode"] == "bad_greeting":
                    reply("invalid greeting")
                    return
                if state["mode"] == "slow_greeting":
                    time.sleep(0.2)
                if state["mode"] == "multiline_overflow":
                    reply("220-Starting")
                    for _ in range(101):
                        reply("intermediate line")
                    return
                # Fragmented multiline greeting, with a different intermediate code.
                self.wfile.write(b"22")
                self.wfile.flush()
                reply("0-Test FTP")
                reply("intermediate greeting")
                reply("123 Not the terminator")
                reply("220 Ready")
                while wire := self.rfile.readline():
                    text = wire.decode("utf-8").rstrip("\r\n")
                    verb, _, argument = text.partition(" ")
                    state["commands"].append((verb, "<redacted>" if verb == "PASS" else argument))
                    if verb == "USER":
                        reply(
                            "230 Already logged in"
                            if state["mode"] == "user230"
                            else "331 Password required"
                        )
                    elif verb == "PASS":
                        reply(
                            "530 Login failed"
                            if argument != "lab-only" or state["mode"] == "authfail"
                            else "230 Logged in"
                        )
                    elif verb == "TYPE":
                        reply(
                            "502 Not implemented"
                            if state["mode"] == "typefail"
                            else "200 Binary mode"
                        )
                    elif verb == "SIZE":
                        if state["mode"] == "no_size":
                            reply("502 Unsupported")
                        elif state["mode"] == "bad_size":
                            reply("213 not-a-number")
                        elif argument not in state["files"]:
                            reply("550 Not found")
                        else:
                            size = len(state["files"][argument]) + (
                                3 if state["mode"] == "size_mismatch" else 0
                            )
                            if state["mode"] == "multiline_size":
                                reply("213-Size information")
                                reply("intermediate line")
                            reply(f"213 {size}")
                    elif verb == "PASV":
                        if passive:
                            passive.close()
                        passive = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        passive.bind(("127.0.0.1", 0))
                        passive.listen(1)
                        passive.settimeout(3)
                        port = passive.getsockname()[1]
                        if state["mode"] == "bad_pasv":
                            reply("227 Entering (127,0,0,1,999,1)")
                        elif state["mode"] == "zero_pasv":
                            reply("227 Entering (127,0,0,1,0,0)")
                        elif state["mode"] == "unreachable_pasv":
                            passive.close()
                            passive = None
                            reply(f"227 Entering (127,0,0,1,{port // 256},{port % 256})")
                        else:
                            host = "203,0,113,5" if state["mode"] == "foreign_pasv" else "127,0,0,1"
                            # Deliberately omit parentheses (allowed by RFC 1123).
                            reply(f"227 Entering Passive Mode {host},{port // 256},{port % 256}")
                    elif verb in {"LIST", "STOR", "RETR"}:
                        if verb == "RETR" and argument not in state["files"]:
                            reply("550 Not found")
                            if passive:
                                passive.close()
                                passive = None
                            continue
                        if passive is None:
                            reply("425 No data connection")
                            continue
                        reply("150 Opening data connection")
                        data, _ = passive.accept()
                        passive.close()
                        passive = None
                        with data:
                            data.settimeout(3)
                            if verb == "STOR":
                                content = bytearray()
                                while chunk := data.recv(65536):
                                    content.extend(chunk)
                                state["files"][argument] = bytes(content)
                                if state["mode"] == "corrupt_upload":
                                    state["files"][argument] += b"corrupted"
                            else:
                                if state["mode"] == "data_stall":
                                    state["release"].wait(timeout=2)
                                if verb == "LIST":
                                    payload = "".join(
                                        f"-rw-r--r-- 1 lab lab {len(value)} Jan 1 00:00 {name}\r\n"
                                        for name, value in sorted(state["files"].items())
                                    ).encode("utf-8")
                                else:
                                    payload = state["files"][argument]
                                if state["mode"] == "slow_data":
                                    for byte in payload:
                                        data.sendall(bytes([byte]))
                                        time.sleep(0.03)
                                else:
                                    data.sendall(payload)
                        if state["mode"] == "control_eof":
                            return
                        reply(
                            "426 Transfer failed"
                            if state["mode"] == "bad_final"
                            else "226 Transfer complete"
                        )
                    elif verb == "QUIT":
                        reply("221 Bye")
                        return
                    else:
                        reply("500 Unknown command")
            except OSError:
                return
            finally:
                if passive:
                    passive.close()

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            yield FTPSettings(port=server.server_address[1], timeout=1), state
        finally:
            state["release"].set()
            server.shutdown()
            thread.join(timeout=3)
            assert not thread.is_alive()


@contextmanager
def dns_socket_pair(udp_handler, tcp_handler):
    # Reserving UDP first can pick a port occupied by TCP or in TCP TIME_WAIT.
    # Bind TCP first, then reserve UDP on the same port; retry only actual clashes.
    for _ in range(20):
        tcp = socketserver.ThreadingTCPServer(("127.0.0.1", 0), tcp_handler)
        try:
            udp = socketserver.ThreadingUDPServer(tcp.server_address, udp_handler)
        except OSError as exc:
            tcp.server_close()
            if exc.errno != errno.EADDRINUSE:
                raise
        else:
            break
    else:
        raise RuntimeError("No free paired TCP/UDP port for the DNS test server")
    try:
        yield udp, tcp
    finally:
        udp.server_close()
        tcp.server_close()


@pytest.fixture
def dns_server():
    queries = []
    lock = threading.Lock()

    def respond(wire, transport):
        query = dns.message.from_wire(wire)
        question = query.question[0]
        name = question.name.to_text()
        record_type = dns.rdatatype.to_text(question.rdtype)
        with lock:
            queries.append((name, record_type, transport))
        answer = dns.message.make_response(query)
        answer.flags |= dns.flags.AA
        if name == "timeout.monitor.test.":
            return None
        if name == "missing.monitor.test.":
            answer.set_rcode(dns.rcode.NXDOMAIN)
        elif name == "failed.monitor.test.":
            answer.set_rcode(dns.rcode.SERVFAIL)
        elif name == "refused.monitor.test.":
            answer.set_rcode(dns.rcode.REFUSED)
        elif name == "truncated.monitor.test." and transport == "udp":
            answer.flags |= dns.flags.TC
        else:
            if name == "alias.monitor.test.":
                answer.answer.append(
                    dns.rrset.from_text(name, 60, "IN", "CNAME", "router.monitor.test.")
                )
                name = "router.monitor.test."
            records = {
                ("router.monitor.test.", "A"): ("127.0.0.1", "127.0.0.2"),
                ("router.monitor.test.", "AAAA"): ("::1",),
                ("device.monitor.test.", "A"): ("127.0.0.1",),
                ("ftp.monitor.test.", "A"): ("127.0.0.1",),
                ("monitor.test.", "MX"): ("10 mail.monitor.test.", "20 backup.monitor.test."),
                ("truncated.monitor.test.", "A"): ("127.0.0.3",),
            }
            values = records.get((name, record_type))
            if values:
                answer.answer.append(dns.rrset.from_text(name, 60, "IN", record_type, *values))
        return answer.to_wire()

    class UDPHandler(socketserver.BaseRequestHandler):
        def handle(self):
            answer = respond(self.request[0], "udp")
            if answer is not None:
                self.request[1].sendto(answer, self.client_address)

    class TCPHandler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(2)
            prefix = self.rfile.read(2)
            if len(prefix) != 2:
                return
            answer = respond(self.rfile.read(struct.unpack("!H", prefix)[0]), "tcp")
            if answer is not None:
                self.wfile.write(struct.pack("!H", len(answer)) + answer)

    with dns_socket_pair(UDPHandler, TCPHandler) as (udp, tcp):
        threads = [
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
            for server in (udp, tcp)
        ]
        for thread in threads:
            thread.start()
        try:
            yield DNSSettings(host="127.0.0.1", port=udp.server_address[1], timeout=1), queries
        finally:
            udp.shutdown()
            tcp.shutdown()
            for thread in threads:
                thread.join(timeout=3)
                assert not thread.is_alive()


@pytest.fixture
def snmp_agent():
    behavior = {"mode": "normal", "requests": []}
    values = {
        SYSTEM_OIDS["description"]: rfc1902.OctetString("Agente de prueba"),
        SYSTEM_OIDS["object_id"]: rfc1902.ObjectIdentifier("1.3.6.1.4.1.8072"),
        SYSTEM_OIDS["uptime_ticks"]: rfc1902.TimeTicks(123456),
        SYSTEM_OIDS["contact"]: rfc1902.OctetString("admin@monitor.test"),
        SYSTEM_OIDS["name"]: rfc1902.OctetString("router.monitor.test"),
        SYSTEM_OIDS["location"]: rfc1902.OctetString("Laboratorio"),
        SYSTEM_OIDS["interface_count"]: rfc1902.Integer32(1),
        INTERFACE_ROOT + ".1.2": rfc1902.Integer32(2),
        INTERFACE_ROOT + ".2.2": rfc1902.OctetString("eth0"),
        INTERFACE_ROOT + ".6.2": rfc1902.OctetString(b"\x00\x11\x22\x33\x44\xff"),
        INTERFACE_ROOT + ".8.2": rfc1902.Integer32(1),
        INTERFACE_ROOT + ".10.2": rfc1902.Counter32(4294967295),
        INTERFACE_ROOT + ".16.2": rfc1902.Counter32(345678),
        "1.3.6.1.2.1.31.1.1.1.6.2": rfc1902.Counter64(2**60),
    }
    indexed = {tuple(map(int, oid.split("."))): value for oid, value in values.items()}

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            message, remaining = decoder.decode(self.request[0], asn1Spec=v2c.Message())
            assert not remaining
            pdu = v2c.apiMessage.get_pdu(message)
            is_next = pdu.tagSet == v2c.GetNextRequestPDU.tagSet
            bindings = v2c.apiPDU.get_varbinds(pdu)
            community = str(v2c.apiMessage.get_community(message))
            behavior["requests"].append((is_next, [str(oid) for oid, _ in bindings], community))
            if behavior["mode"] == "drop" or community != "monitorlab":
                return
            response = v2c.apiMessage.get_response(message)
            response_pdu = v2c.apiMessage.get_pdu(response)
            if behavior["mode"] == "error":
                v2c.apiPDU.set_varbinds(response_pdu, bindings)
                v2c.apiPDU.set_error_status(response_pdu, 5)
                v2c.apiPDU.set_error_index(response_pdu, 1)
            else:
                returned = []
                for oid, _ in bindings:
                    key = tuple(oid)
                    if is_next:
                        next_key = next((k for k in sorted(indexed) if k > key), None)
                        if behavior["mode"] == "stuck":
                            returned.append((oid, rfc1902.Integer32(1)))
                        elif next_key:
                            returned.append((next_key, indexed[next_key]))
                        else:
                            returned.append((oid, rfc1905.endOfMibView))
                    else:
                        returned.append((oid, indexed.get(key, rfc1905.noSuchObject)))
                if behavior["mode"] == "mismatch":
                    returned.reverse()
                v2c.apiPDU.set_varbinds(response_pdu, returned)
            self.request[1].sendto(encoder.encode(response), self.client_address)

    with socketserver.ThreadingUDPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            yield SNMPSettings(port=server.server_address[1], timeout=0.1, retries=0), behavior
        finally:
            server.shutdown()
            thread.join(timeout=3)
            assert not thread.is_alive()
