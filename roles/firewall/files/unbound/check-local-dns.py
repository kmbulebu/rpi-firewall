#!/usr/bin/python3
"""Check a configured local SOA without depending on upstream connectivity."""
import sys

import dns.message
import dns.query
import dns.rcode
import dns.rdatatype


def check(server, domain):
    query = dns.message.make_query(domain, 'SOA')
    response = dns.query.udp(query, server, timeout=3)
    if response.rcode() != dns.rcode.NOERROR or not any(
        record.rdtype == dns.rdatatype.SOA and record.name == query.question[0].name
        for record in response.answer
    ):
        raise RuntimeError('Local DNS did not return the configured SOA')
    print('Local DNS is responding')


if __name__ == '__main__':
    try:
        check(*sys.argv[1:])
    except Exception as error:
        print(f'Local DNS health check failed: {error}', file=sys.stderr)
        sys.exit(1)
