"""Read-only W/committed-S verification; no application/test imports or execution."""
from pathlib import Path
from urllib.parse import unquote
import hashlib
import json
import re
import argparse
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = next(p for p in HERE.parents if (p / 'AGENTS.md').is_file())
manifest = json.loads((HERE / 'migration-map.json').read_text(encoding='utf-8'))
parser = argparse.ArgumentParser()
parser.add_argument('--scope', choices=('working', 'committed'), default='working')
scope = parser.parse_args().scope
failures = []
git_files = set()
if scope == 'committed':
    git_files = set(subprocess.run(['git', 'ls-tree', '-r', '--name-only', '-z', 'HEAD'], cwd=ROOT,
                                  check=True, stdout=subprocess.PIPE).stdout.decode('utf-8').split('\0'))

def content(rel):
    if scope == 'committed':
        return subprocess.run(['git', 'show', 'HEAD:' + rel], cwd=ROOT, check=True,
                              stdout=subprocess.PIPE).stdout.decode('utf-8-sig').replace('\r\n', '\n')
    return (ROOT / rel).read_text(encoding='utf-8-sig')

def exists(rel):
    if scope == 'committed':
        rel = Path(rel).as_posix()
        return rel in git_files or any(p.startswith(rel.rstrip('/') + '/') for p in git_files)
    return (ROOT / rel).exists()

hashes = manifest['document_hashes'] if scope == 'working' else manifest['committed_document_hashes']
assertions = manifest['rule_assertions'] if scope == 'working' else manifest['committed_rule_assertions']
for rel, expected in hashes.items():
    path = ROOT / rel
    if not exists(rel):
        failures.append('Missing migrated document: ' + rel)
    elif hashlib.sha256(content(rel).encode('utf-8')).hexdigest() != expected:
        failures.append('Migration snapshot content changed: ' + rel)

root_text = content('AGENTS.md')
if len(root_text.encode('utf-8')) >= 10240:
    failures.append('Root exceeds 10 KiB')
for marker in ('DDL', 'TRUNCATE', '隔离测试库', '--allow-live', '--allow-external',
               '用户手动', '来源/账户', '未知', '不可变历史', 'git add -A', '非 trivial'):
    if marker not in root_text:
        failures.append('Missing resident gate: ' + marker)
for item in assertions:
    if item['label'] not in content(item['target']):
        failures.append('Missing original rule label: ' + item['label'])

actual = ({p.relative_to(ROOT).as_posix() for p in (ROOT / 'docs/experience').rglob('*') if p.is_file()}
          if scope == 'working' else {p for p in git_files if p.startswith('docs/experience/')})
expected = {r['to'] for r in manifest['experience_files'] if scope == 'working' or r['originally_tracked']}
if actual != expected:
    failures.append('Experience file inventory mismatch')
if exists('docs/memory'):
    failures.append('Old experience directory still exists')

# New entries must have existing file/directory links and valid local heading anchors.
entries = ['AGENTS.md'] + sorted(p for p in hashes if p.startswith('docs/standards/') and p.endswith('.md'))
if scope == 'working':
    entries += sorted(p.relative_to(ROOT).as_posix() for p in HERE.parent.rglob('*.md'))
else:
    task_prefix = HERE.parent.relative_to(ROOT).as_posix() + '/'
    entries += sorted(p for p in git_files if p.startswith(task_prefix) and p.endswith('.md'))
for rel in entries:
    text = content(rel)
    text = re.sub(r'^```[^\n]*\n.*?^```\s*$', '', text, flags=re.M | re.S)
    for target in re.findall(r'\]\((<[^>]+>|[^\s)]+)(?:\s+"[^"]*")?\)', text):
        target = unquote(target.strip('<>'))
        if re.match(r'^[a-zA-Z][\w+.-]*:', target) or target.startswith('//'):
            continue
        location, _, anchor = target.partition('#')
        path = (ROOT / location.lstrip('/')) if location.startswith('/') else ((ROOT / rel).parent / location)
        target_rel = path.resolve().relative_to(ROOT).as_posix()
        if not exists(target_rel):
            failures.append('Broken new link: ' + rel + ' -> ' + target)
        elif anchor and path.suffix == '.md' and not re.fullmatch(r'L\d+', anchor):
            headings = re.findall(r'^#{1,6}\s+(.+)$', content(target_rel), re.M)
            anchors = {re.sub(r'[^\w\s\-]', '', h).strip().lower().replace(' ', '-') for h in headings}
            if anchor.lower() not in anchors:
                failures.append('Broken new anchor: ' + rel + ' -> ' + target)
    if rel == 'AGENTS.md' or rel.startswith('docs/standards/'):
        if 'CLAUDE.md' in text or re.search(r'(?<![\w.-])memory[/\\]', text):
            failures.append('Stale active instruction pointer: ' + rel)

print(json.dumps({'result': 'PASS' if not failures else 'FAIL',
                  'scope': scope,
                  'root_bytes': len(root_text.encode('utf-8')),
                  'experience_files': len(actual),
                  'source_lines_mapped': len(manifest['rule_mapping']),
                  'rule_labels_checked': len(assertions),
                  'snapshot_documents': len(hashes),
                  'failures': failures}, ensure_ascii=True))
raise SystemExit(bool(failures))
