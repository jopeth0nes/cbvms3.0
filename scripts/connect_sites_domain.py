"""Point only studentportal.bond and its Sites verification records to Sites."""
import argparse
import base64
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = 'studentportal.bond'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    desired = json.loads((ROOT / 'docs/studentportal-sites-dns.json').read_text())
    if desired['domain'] != DOMAIN:
        raise ValueError('Unexpected domain')
    certificate = Path.home() / '.cloudflared/cert.pem'
    cert = json.loads(base64.b64decode(''.join(line for line in certificate.read_text().splitlines() if not line.startswith('---'))))
    base = 'https://api.cloudflare.com/client/v4/zones/' + cert['zoneID'] + '/dns_records'

    def api(method, suffix='', value=None):
        request = Request(base + suffix, method=method,
            data=None if value is None else json.dumps(value).encode(),
            headers={'Authorization': 'Bearer ' + cert['apiToken'], 'Content-Type': 'application/json'})
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
        if not result.get('success'):
            raise RuntimeError('Cloudflare DNS request failed')
        return result.get('result')

    names = {DOMAIN}
    for item in desired['records']:
        if item['type'] == 'TXT':
            name = item['name'].rstrip('.')
            if name not in {'_openai-site-verification.' + DOMAIN, '_cf-custom-hostname.' + DOMAIN}:
                raise ValueError('Unexpected verification record')
            names.add(name)
        elif item['type'] != 'A' or item['name'] != '@':
            raise ValueError('Unexpected routing record')
    existing = []
    for name in sorted(names):
        existing.extend(api('GET', '?' + urlencode({'name': name, 'per_page': 100})))
    operations = []
    addresses = [r for r in existing if r['name'] == DOMAIN and r['type'] == 'A']
    for item in [r for r in desired['records'] if r['type'] == 'A']:
        value = {'type': 'A', 'name': DOMAIN, 'content': item['value'], 'proxied': False, 'ttl': 300}
        match = next((r for r in addresses if r['content'] == item['value']), None)
        if match:
            addresses.remove(match)
            if match.get('proxied') or match['ttl'] != 300:
                operations.append(('PATCH', '/' + match['id'], value))
        elif addresses:
            match = addresses.pop(0)
            operations.append(('PATCH', '/' + match['id'], value))
        else:
            operations.append(('POST', '', value))
    for record in addresses + [r for r in existing if r['name'] == DOMAIN and r['type'] in {'AAAA', 'CNAME'}]:
        operations.append(('DELETE', '/' + record['id'], None))
    for item in [r for r in desired['records'] if r['type'] == 'TXT']:
        if not any(r['type'] == 'TXT' and r['name'] == item['name'] and r['content'] == item['value'] for r in existing):
            operations.append(('POST', '', {'type': 'TXT', 'name': item['name'], 'content': item['value'], 'ttl': 300}))
    for method, _, value in operations:
        print(json.dumps({'operation': method, 'record': value}))
    if not args.apply:
        print('Dry run only; DNS unchanged.')
        return
    backup = ROOT / 'data/sites-domain-dns-before.json'
    if not backup.exists():
        backup.write_text(json.dumps(existing, indent=2), encoding='utf-8')
    for method, suffix, value in operations:
        api(method, suffix, value)
    print(f'Applied {len(operations)} DNS changes for {DOMAIN}.')


if __name__ == '__main__':
    main()
