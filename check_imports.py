import subprocess, sys, os

files = [
    'core/models.py',
    'core/ingestion.py',
    'core/extraction.py',
    'core/resolution.py',
    'integrations/base.py',
    'integrations/github_connector.py',
    'integrations/github_client.py',
    'integrations/slack_connector.py',
    'storage/audit_log.py',
    'graph.py',
    'config.py',
    'app.py',
    'dry_run.py',
]

print("=" * 60)
print("Syntax check (AST parse)")
print("=" * 60)

errors = []
for f in files:
    if not os.path.exists(f):
        print(f"  [SKIP] {f} (not found)")
        continue
    try:
        import ast
        with open(f, encoding='utf-8') as fh:
            source = fh.read()
        ast.parse(source)
        print(f"  [OK]   {f}")
    except SyntaxError as e:
        print(f"  [ERR]  {f}  →  Line {e.lineno}: {e.msg}")
        errors.append((f, e))

print()
print("=" * 60)
print("Import check (actual import attempt)")
print("=" * 60)

import_map = {
    'core/models.py':              'core.models',
    'core/ingestion.py':           'core.ingestion',
    'core/extraction.py':          'core.extraction',
    'core/resolution.py':          'core.resolution',
    'integrations/base.py':        'integrations.base',
    'integrations/github_connector.py': 'integrations.github_connector',
    'integrations/slack_connector.py':  'integrations.slack_connector',
    'storage/audit_log.py':        'storage.audit_log',
    'graph.py':                    'graph',
    'config.py':                   'config',
}

for filepath, module in import_map.items():
    if not os.path.exists(filepath):
        print(f"  [SKIP] {module}")
        continue
    result = subprocess.run(
        [sys.executable, '-c', f'import {module}; print("  [OK]   {module}")'],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"  [ERR]  {module}")
        # Print just the last relevant error line
        stderr_lines = [l for l in result.stderr.strip().splitlines() if l.strip()]
        for line in stderr_lines[-3:]:
            print(f"         {line}")
    else:
        print(result.stdout.strip())

print()
if errors:
    print(f"SYNTAX ERRORS FOUND: {len(errors)}")
else:
    print("All syntax checks passed.")
