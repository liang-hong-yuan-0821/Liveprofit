"""Generate and search a test-file catalog without importing or executing tests."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys

from tests.support.python.paths import PROJECT_ROOT

BEGIN = 'test-catalog-begin'
END = 'test-catalog-end'
ENVIRONMENTS = frozenset({'local', 'db', 'redis', 'real_llm', 'external_data', 'browser', 'app'})
JSON_PATH = 'tests/catalog.json'
DOC_PATH = 'docs/knowledge/test/测试索引.md'


def discover(root: Path) -> list[Path]:
    files = []
    for domain in ('backend', 'frontend', 'ai', 'data', 'e2e'):
        directory = root / 'tests' / domain
        if directory.exists():
            files.extend(p for p in directory.rglob('*') if p.is_file() and (
                (p.name.startswith('test_') and p.suffix == '.py')
                or p.name.endswith(('.test.ts', '.test.tsx', '.spec.ts'))
            ) and not {'support', 'fixtures', '__pycache__'}.intersection(p.relative_to(directory).parts))
    return sorted(files)


def read_metadata(source: str) -> dict:
    """Metadata is a JSON object inside a commented header, before executable code."""
    lines = source.lstrip('\ufeff').splitlines()
    positions = [i for i, line in enumerate(lines) if line.strip() in ('# ' + BEGIN, '// ' + BEGIN)]
    if len(positions) != 1:
        raise ValueError('Exactly one test-catalog header is required')
    start = positions[0]
    prefix = '# ' if lines[start].strip().startswith('#') else '// '
    if any(line.strip() and not line.lstrip().startswith(prefix.rstrip()) for line in lines[:start]):
        raise ValueError('Catalog header must precede executable code and module docstrings')
    payload = []
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped == prefix + END:
            metadata = json.loads('\n'.join(payload))
            if not isinstance(metadata, dict):
                raise ValueError('Metadata must be an object')
            return metadata
        if not stripped.startswith(prefix):
            raise ValueError('Every metadata line must be commented')
        payload.append(stripped[len(prefix):])
    raise ValueError('Unclosed catalog header')


def validated_path(root: Path, value: str) -> str:
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError(f'Expected a repository-relative POSIX path: {value!r}')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or ':' in value:
        raise ValueError(f'Path must stay inside the repository: {value!r}')
    target = (root / value).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise ValueError(f'Missing file or out-of-repository path: {value}')
    return path.as_posix()


def scenarios(source: str, suffix: str) -> list[dict]:
    if suffix == '.py':
        tree = ast.parse(source)
        found = []
        for node in tree.body:
            group = [(node, '')] if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else []
            if isinstance(node, ast.ClassDef) and node.name.startswith('Test'):
                group = [(child, node.name + '.') for child in node.body
                         if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))]
            for case, owner in group:
                decorators = [ast.unparse(d.func if isinstance(d, ast.Call) else d) for d in case.decorator_list]
                if case.name.startswith('test_') and not any(d.rsplit('.', 1)[-1] in {'fixture', 'contextmanager'} for d in decorators):
                    doc = ast.get_docstring(case) or ''
                    found.append({'name': owner + case.name, 'line': case.lineno,
                                  'description': doc.splitlines()[0] if doc else ''})
        return found
    # Static test titles only; dynamic/parameterized titles remain templates.
    pattern = r'\b(?:it|test)(?:\.(?:only|skip|todo))?\s*\(\s*([\'"`])((?:\\.|(?!\1).)*?)\1'
    return [{'name': m.group(2), 'line': source.count('\n', 0, m.start()) + 1, 'description': ''}
            for m in re.finditer(pattern, source, re.DOTALL)]


def build_catalog(root: Path = PROJECT_ROOT) -> dict:
    suites = json.loads((root / 'tests/suites.json').read_text(encoding='utf-8'))['modules']
    files = discover(root)
    known = {p.relative_to(root).as_posix() for p in files}
    errors, entries = [], []
    for name, suite in suites.items():
        base = (root / suite['test_path']).resolve()
        if not base.is_relative_to((root / 'tests').resolve()) or not base.is_dir():
            errors.append(f'{name}: invalid module test path')
        if set(suite['related']) - suites.keys():
            errors.append(f'{name}: unknown related module')
    for file in files:
        path = file.relative_to(root).as_posix()
        try:
            source = file.read_text(encoding='utf-8-sig')
            metadata = read_metadata(source)
            unknown = metadata.keys() - {'purpose', 'keywords', 'covers', 'environment', 'related_tests'}
            if unknown:
                raise ValueError(f'Unknown metadata fields: {sorted(unknown)}')
            purpose = metadata.get('purpose')
            if not isinstance(purpose, str) or not purpose.strip():
                raise ValueError('A nonempty purpose is required')
            for field in ('keywords', 'covers', 'environment'):
                values = metadata.get(field)
                if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values):
                    raise ValueError(f'{field} must be a nonempty string list')
                if len(values) != len(set(values)):
                    raise ValueError(f'{field} contains duplicates')
            covers = [validated_path(root, p) for p in metadata['covers']]
            if set(metadata['environment']) - ENVIRONMENTS:
                raise ValueError('Unknown environment label')
            related = metadata.get('related_tests', [])
            if not isinstance(related, list) or any(p not in known or p == path for p in related) or len(related) != len(set(related)):
                raise ValueError('related_tests must name distinct other formal test files')
            owners = [name for name, s in suites.items() if path.startswith(s['test_path'].rstrip('/') + '/')]
            if len(owners) != 1:
                raise ValueError(f'Expected exactly one module owner, got {owners}')
            module = owners[0]
            level = file.relative_to(root / suites[module]['test_path']).parts[0]
            if level not in {'unit', 'integration', 'contract', 'api', 'ui'}:
                raise ValueError(f'Unknown test level: {level}')
            entries.append({'path': path, 'module': module, 'level': level, **metadata,
                            'covers': covers, 'related_tests': related,
                            'related_modules': suites[module]['related'],
                            'scenarios': scenarios(source, file.suffix),
                            'source_sha256': hashlib.sha256(source.encode('utf-8')).hexdigest()})
        except (ValueError, SyntaxError, KeyError, TypeError) as exc:
            errors.append(f'{path}: {exc}')
    if errors:
        raise ValueError('\n'.join(errors))
    reverse = {}
    for entry in entries:
        for path in entry['covers']:
            reverse.setdefault(path, []).append(entry['path'])
    return {'schema_version': 1, 'notice': 'Declared coverage locates candidates; read assertions and fixtures before changing or running tests.',
            'test_file_count': len(entries), 'tests': entries, 'by_source': dict(sorted(reverse.items()))}


def escape(value: str) -> str:
    return value.replace('|', '\\|').replace('\n', ' ').replace('<', '&lt;').replace('>', '&gt;')


GENERATED = 'Generated by python -m tests.index'
DETAIL_JSON_DIR = 'tests/catalog'
DETAIL_DOC_DIR = 'docs/knowledge/test/测试详情'


def module_paths(module: str) -> tuple[str, str]:
    # Module names are identifiers, never filesystem input.
    if not re.fullmatch(r'[a-zA-Z_][a-zA-Z_0-9]*(?:\.[a-zA-Z_][a-zA-Z_0-9]*)+', module):
        raise ValueError(f'Invalid module name: {module}')
    relative = module.replace('.', '/')
    return f'{DETAIL_JSON_DIR}/{relative}.json', f'{DETAIL_DOC_DIR}/{relative}.md'


def short_entry(entry: dict) -> dict:
    json_path, doc_path = module_paths(entry['module'])
    return {key: value for key, value in entry.items() if key not in {'scenarios', 'related_modules', 'related_tests'}} | {
        'scenario_count': len(entry['scenarios']), 'detail_json': json_path, 'detail_doc': doc_path,
    }


def render_markdown(catalog: dict) -> str:
    lines = ['# 测试文件索引', '', f'<!-- {GENERATED}; edit test-file metadata. -->', '',
             '先查询候选文件，再读取对应模块详情和测试断言、fixture。详情按业务模块生成，无需通读全部测试。', '',
             '```text', 'python -m tests.index --query "行情刷新 复用"',
             'python -m tests.index --source backend/modules/market_data/application/refresh_service.py',
             'python -m tests.index --module backend.market_data --details', '```', '',
             '默认返回简短定位信息；`--details` 展开场景及关联回归，`--json` 可输出相应结构。', '',
             '覆盖登记用于定位，不能证明断言充分或授权执行真实依赖。场景声明数量不代表参数化后的执行数量。', '',
             '修改测试顶部说明或测试场景后运行 `python -m tests.index`；收尾运行 `python -m tests.index --check`。', '',
             f'共 {catalog["test_file_count"]} 个正式测试文件。', '',
             '| 模块详情 | 文件数 | 场景声明数 | 依赖环境 |', '|---|---:|---:|---|']
    groups = {}
    for entry in catalog['tests']:
        groups.setdefault(entry['module'], []).append(entry)
    for module, entries in sorted(groups.items()):
        doc = module_paths(module)[1].removeprefix(str(PurePosixPath(DOC_PATH).parent) + '/')
        env = sorted({v for e in entries for v in e['environment']})
        lines.append(f'| [{module}]({doc}) | {len(entries)} | {sum(len(e["scenarios"]) for e in entries)} | {", ".join(env)} |')
    return '\n'.join(lines) + '\n'


def render_details(module: str, entries: list[dict]) -> str:
    doc_path = module_paths(module)[1]
    # Resolve links from the actual module document depth.
    prefix = '../' * (len(PurePosixPath(doc_path).parts) - 1)
    def link(path):
        return prefix + path
    lines = [f'# {module} 测试详情', '', f'<!-- {GENERATED}; edit test-file metadata. -->', '',
             f'[返回测试索引]({link(DOC_PATH)}) · 共 {len(entries)} 个文件。', '',
             '先按用途定位文件，再检查场景、断言与 fixture。依赖标签不代替隔离核查。', '']
    for entry in entries:
        lines += [f'## {entry["path"].split("/")[-1]}', '',
                  f'源码：[{entry["path"]}]({link(entry["path"])})', '',
                  escape(entry['purpose']), '',
                  f'层级：{entry["level"]}；环境：{", ".join(entry["environment"])}。', '',
                  '关键词：' + escape('、'.join(entry['keywords'])), '', '被测代码：', '']
        lines += [f'- [{p}]({link(p)})' for p in entry['covers']]
        lines += ['', '关联模块：' + ('、'.join(entry['related_modules']) or '无显式登记') + '。', '']
        lines += [f'- 关联文件：[{p}]({link(p)})' for p in entry['related_tests']]
        lines += [f'场景声明（{len(entry["scenarios"])}）：', '']
        for case in entry['scenarios']:
            name = escape(case['name']).replace('`', "'")
            desc = ' — ' + escape(case['description']) if case['description'] else ''
            lines.append(f'- [{name}]({link(entry["path"])}#L{case["line"]}){desc}')
        if not entry['scenarios']:
            lines.append('- 动态场景请直接阅读源码。')
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def generated_outputs(catalog: dict) -> dict[str, str]:
    groups = {}
    for entry in catalog['tests']:
        groups.setdefault(entry['module'], []).append(entry)
    summaries = [short_entry(e) for e in catalog['tests']]
    compact = {'schema_version': 2, 'generated_by': GENERATED, 'notice': catalog['notice'],
               'test_file_count': len(summaries), 'tests': summaries}
    outputs = {JSON_PATH: json.dumps(compact, ensure_ascii=False, indent=2) + '\n', DOC_PATH: render_markdown(catalog)}
    for module, entries in sorted(groups.items()):
        json_path, doc_path = module_paths(module)
        details = {'schema_version': 2, 'generated_by': GENERATED, 'module': module, 'tests': entries}
        outputs[json_path] = json.dumps(details, ensure_ascii=False, indent=2) + '\n'
        outputs[doc_path] = render_details(module, entries)
    return outputs


def obsolete_outputs(root: Path, outputs: dict[str, str]) -> list[str]:
    obsolete = []
    for directory, suffix in ((DETAIL_JSON_DIR, '.json'), (DETAIL_DOC_DIR, '.md')):
        base = root / directory
        for path in base.rglob('*' + suffix) if base.exists() else []:
            name = path.relative_to(root).as_posix()
            if name in outputs:
                continue
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError(f'Generated path leaves repository: {name}')
            body = path.read_text(encoding='utf-8')
            if suffix == '.json':
                try:
                    managed = json.loads(body).get('generated_by') == GENERATED
                except (ValueError, AttributeError):
                    managed = False
            else:
                managed = f'<!-- {GENERATED}; edit test-file metadata. -->' in body
            if managed:
                obsolete.append(name)
    return sorted(obsolete)


def search(catalog: dict, query: str = '', source: str = '', module: str = '') -> list[dict]:
    source = source.replace('\\', '/').rstrip('/')
    terms = query.casefold().split()
    matches = []
    for entry in catalog['tests']:
        if module and entry['module'] != module:
            continue
        if source and not any(p == source or p.startswith(source + '/') for p in entry['covers']):
            continue
        text = ' '.join([entry['purpose'], *entry['keywords'], entry['path'], *entry['covers'],
                         *[case['name'] + ' ' + case['description'] for case in entry['scenarios']]]).casefold()
        if not all(term in text for term in terms):
            continue
        score = sum(5 if term in entry['purpose'].casefold() or term in ' '.join(entry['keywords']).casefold() else 1 for term in terms)
        matches.append((score, entry))
    return [entry for _, entry in sorted(matches, key=lambda pair: (-pair[0], pair[1]['path']))]


def main(argv=None) -> int:
    # Windows pipe output must remain readable to agents and JSON consumers.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Validate metadata and fail if generated files are stale.')
    parser.add_argument('--query', default='', help='Space-separated keywords, all must match.')
    parser.add_argument('--source', default='', help='Repository-relative source file or directory.')
    parser.add_argument('--module', default='', help='Limit candidates to one exact module.')
    parser.add_argument('--details', action='store_true', help='Include scenario declarations and related regression scope.')
    parser.add_argument('--json', action='store_true', help='Machine-readable search results.')
    parser.add_argument('--limit', type=int, default=10, help='Maximum search results; 0 means all.')
    args = parser.parse_args(argv)
    if args.limit < 0:
        parser.error('--limit must be nonnegative')
    searching = bool(args.query or args.source or args.module)
    if args.check and searching:
        parser.error('--check cannot be combined with search filters')
    if (args.json or args.details) and not searching:
        parser.error('--json and --details require a search filter')
    try:
        catalog = build_catalog()
        if args.module and args.module not in json.loads((PROJECT_ROOT / 'tests/suites.json').read_text(encoding='utf-8'))['modules']:
            raise ValueError(f'Unknown module: {args.module}')
        if args.source:
            source = args.source.replace('\\', '/')
            path = (PROJECT_ROOT / source).resolve()
            if not path.is_relative_to(PROJECT_ROOT.resolve()) or not path.exists():
                raise ValueError('Source must be an existing path inside the repository')
            args.source = path.relative_to(PROJECT_ROOT).as_posix()
        if searching:
            selected = search(catalog, args.query, args.source, args.module)
            shown = selected[:args.limit] if args.limit else selected
            if args.json:
                print(json.dumps({'match_count': len(selected), 'tests': shown if args.details else [short_entry(e) for e in shown]}, ensure_ascii=False, indent=2))
            else:
                print(f'{len(selected)} candidates; showing {len(shown)}. Read assertions and fixtures before acting.')
                for entry in shown:
                    print(f'\n{entry["path"]}\n  {entry["purpose"]}\n  environment: {", ".join(entry["environment"])}')
                    print('  covers: ' + ', '.join(entry['covers']))
                    print('  details: ' + module_paths(entry['module'])[1])
                    if args.details:
                        print('  related: ' + ', '.join(entry['related_modules'] + entry['related_tests']))
                        for case in entry['scenarios']:
                            print(f'  {entry["path"]}:{case["line"]} {case["name"]}' +
                                  (' — ' + case['description'] if case['description'] else ''))
            return 0 if selected else 1
        outputs = generated_outputs(catalog)
        obsolete = obsolete_outputs(PROJECT_ROOT, outputs)
        if args.check:
            stale = obsolete + [name for name, body in outputs.items() if not (PROJECT_ROOT / name).is_file() or (PROJECT_ROOT / name).read_text(encoding='utf-8') != body]
            if stale:
                print('Stale index: ' + ', '.join(stale) + '. Run python -m tests.index.', file=sys.stderr)
                return 1
            print(f'Catalog consistent: {catalog["test_file_count"]} files; metadata, source paths and generated outputs valid.')
        else:
            for name in outputs:
                if not (PROJECT_ROOT / name).resolve().is_relative_to(PROJECT_ROOT.resolve()):
                    raise ValueError(f'Generated path leaves repository: {name}')
            for name, body in outputs.items():
                path = PROJECT_ROOT / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body, encoding='utf-8')
            for name in obsolete:
                (PROJECT_ROOT / name).unlink()
            print(f'Generated {len(outputs)} index/detail documents for {catalog["test_file_count"]} files: {JSON_PATH}, {DOC_PATH}')
        return 0
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
