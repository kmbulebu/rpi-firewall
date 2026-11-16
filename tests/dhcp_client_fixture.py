"""Small DHCP DISCOVER/REQUEST packet fixture for isolated integration tests."""
import json
import os
import socket
import struct
import sys


def exchange(iface, requested=None):
    xid = os.urandom(4)
    mac = b'\x02' + os.urandom(5)
    header = struct.pack('!BBBB4sHH4s4s4s4s16s64s128s', 1, 1, 6, 0, xid, 0, 0x8000,
                         bytes(4), bytes(4), bytes(4), bytes(4), mac.ljust(16, b'\0'), bytes(64), bytes(128))

    def packet(kind, extra=b''):
        result = header + b'\x63\x82\x53\x63\x35\x01' + bytes([kind])
        result += b'\x39\x02\x05\xdc\x37\x09\x01\x03\x06\x0f\x1c\x33\x3a\x3b\x77'
        if requested is not None:
            result += b'\x33\x04' + struct.pack('!I', requested)
        return result + extra + b'\xff'

    def receive(kind):
        while True:
            frame = raw.recv(4096)
            # Receive at layer 2, as a DHCP client does before it has an IPv4 address.
            if len(frame) < 28 or frame[9] != 17:
                continue
            ip_size = (frame[0] & 15) * 4
            if frame[ip_size + 2:ip_size + 4] != struct.pack('!H', 68):
                continue
            reply = frame[ip_size + 8:]
            if len(reply) < 240 or reply[0] != 2 or reply[4:8] != xid:
                continue
            options = {}
            pos = 240
            while pos < len(reply):
                code = reply[pos]
                pos += 1
                if code == 255:
                    break
                if code == 0:
                    continue
                size = reply[pos]
                pos += 1
                options.setdefault(str(code), []).append(reply[pos:pos + size].hex())
                pos += size
            if options.get('53') == [bytes([kind]).hex()]:
                return reply, options

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock, \
            socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.htons(0x0800)) as raw:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, iface.encode() + b'\0')
        sock.bind(('', 68))
        raw.bind((iface, 0))
        raw.settimeout(2)
        for _ in range(8):
            try:
                sock.sendto(packet(1), ('255.255.255.255', 67))
                reply, offer = receive(2)
                extra = b'\x32\x04' + reply[16:20] + b'\x36\x04' + bytes.fromhex(offer['54'][0])
                sock.sendto(packet(3, extra), ('255.255.255.255', 67))
                _, ack = receive(5)
                return {'offer': offer, 'ack': ack}
            except TimeoutError:
                continue
    raise AssertionError('No DHCP exchange on ' + iface)


if __name__ == '__main__':
    print(json.dumps(exchange(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else None)))
