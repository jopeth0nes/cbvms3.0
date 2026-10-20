"""Read-only Codex plugin/config/skill discovery; makes no model request.

Run from the trusted checkout with Python 3.11+ and Codex on PATH.
Codex itself may refresh its normal local cache during discovery.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import queue
import subprocess
import threading
import tomllib

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ('cbvms-ecc', 'codex-on-crack')
EXPECTED = {
    'cbvms-verify', 'cbvms-security-review', 'cbvms-regression-first',
    'cbvms-api-design', 'cbvms-db-migrations', 'cbvms-postgres', 'cbvms-deploy',
    'cbvms-portal-e2e', 'cbvms-vision-eval', 'cbvms-portal-design',
    'crack', 'crack-plan', 'crack-setup',
}


def check_local_files():
    names = []
    for plugin in PLUGINS:
        base = ROOT / 'plugins' / plugin
        manifest = json.loads((base / '.codex-plugin/plugin.json').read_text())
        assert manifest['name'] == plugin
        assert not {'mcpServers', 'apps', 'hooks'} & manifest.keys()
        for skill in (base / 'skills').glob('*/SKILL.md'):
            name = next(line[6:].strip() for line in skill.read_text().splitlines()
                        if line.startswith('name: '))
            assert name == skill.parent.name
            names.append(name)
    assert len(names) == len(set(names)) == 13 and set(names) == EXPECTED
    roles = tomllib.loads((ROOT / '.codex/crack/crack.toml').read_text())['roles']
    assert list(roles) == ['builder'] and 'fallback' not in roles['builder']
    role_files = list((ROOT / '.codex/agents').glob('*.toml'))
    assert [p.name for p in role_files] == ['crack_builder.toml']
    role = tomllib.loads(role_files[0].read_text())
    assert role['model'] == roles['builder']['model'] == 'gpt-6-luna'
    assert role['model_reasoning_effort'] == roles['builder']['effort'] == 'medium'
    assert role['agents']['enabled'] is False
    provenance = json.loads((ROOT / 'plugins/codex-on-crack/UPSTREAM.json').read_text())
    for path, entry in provenance['files'].items():
        actual = hashlib.sha256((ROOT / 'plugins/codex-on-crack' / path).read_bytes()).hexdigest()
        assert actual == entry['installed_sha256'], f'Vendored source drift: {path}'


def main():
    check_local_files()
    proc = subprocess.Popen(['codex', 'app-server', '--stdio', '--strict-config'],
                            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True)
    messages = queue.Queue()

    def read_lines():
        for line in proc.stdout:
            try:
                messages.put(json.loads(line))
            except json.JSONDecodeError:
                continue
        messages.put({'closed': True})

    threading.Thread(target=read_lines, daemon=True).start()
    counter = 0

    def request(method, params):
        nonlocal counter
        counter += 1
        proc.stdin.write(json.dumps({'id': counter, 'method': method, 'params': params}) + '\n')
        proc.stdin.flush()
        while True:
            msg = messages.get(timeout=45)
            if msg.get('closed'):
                raise RuntimeError('Codex app-server closed before discovery completed')
            if msg.get('id') == counter:
                if 'error' in msg:
                    raise RuntimeError(f'{method}: {msg["error"]}')
                return msg['result']

    try:
        request('initialize', {'clientInfo': {'name': 'cbvms_tooling_check', 'version': '1.0'},
                               'capabilities': {'experimentalApi': True}})
        proc.stdin.write('{"method":"initialized","params":{}}\n')
        proc.stdin.flush()
        effective = request('config/read', {'cwd': str(ROOT), 'includeLayers': False})['config']
        assert effective['model'] == 'gpt-6-astra'
        assert effective['agents']['max_concurrent_threads_per_session'] == 1
        for plugin in PLUGINS:
            assert effective['plugins'][f'{plugin}@cbvms-local']['enabled'] is True
        result = request('skills/list', {'cwds': [str(ROOT)], 'forceReload': True})
        entry = result['data'][0]
        assert not entry.get('errors'), f'Skill loading errors: {entry.get("errors")}'
        found = [s for s in entry['skills'] if s['name'].split(':')[-1] in EXPECTED]
        names = [s['name'].split(':')[-1] for s in found]
        assert len(names) == len(set(names)) == 13 and set(names) == EXPECTED, names
        assert all(s.get('enabled', True) for s in found)
        outside = request('config/read', {'cwd': '/private/tmp', 'includeLayers': False})['config']
        assert not any(f'{p}@cbvms-local' in outside.get('plugins', {}) for p in PLUGINS)
        outside_skills = request('skills/list', {'cwds': ['/private/tmp'], 'forceReload': True})
        assert not any(s['name'].split(':')[-1] in EXPECTED
                       for row in outside_skills['data'] for s in row['skills']), 'CBVMS skills leaked outside project'
        print(json.dumps({'config': 'loaded by Codex with strict parsing',
                          'lead': effective['model'], 'max_workers': 1,
                          'project_scope': 'no CBVMS plugin settings outside repository',
                          'discovered_skills': sorted(names),
                          'skill_paths': [s['path'] for s in found],
                          'node_version': subprocess.check_output(['node', '--version'], text=True).strip(),
                          'required_node': '>=24.15.0 (upstream supported minimum)',
                          'model_requests': 0}, indent=2))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


if __name__ == '__main__':
    main()
