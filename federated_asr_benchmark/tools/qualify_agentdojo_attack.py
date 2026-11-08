"""Offline native attack-delivery/scorer rehearsal; no product execution or API requests."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request
import uuid

PACKAGE = Path(__file__).resolve().parents[1]
PROFILE_ID = 'codex_deepseek_flash_agentdojo_banking_v1'
IMAGE = 'agent-safety-agentdojo-attack-replay:v1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_sources():
    runtime = PACKAGE / 'runtime/deepseek_smoke'
    sources = {'Dockerfile': runtime / 'AttackReplay.Dockerfile',
        'attack_replay.py': runtime / 'attack_replay.py',
        'agentdojo_mcp_server.py': PACKAGE / 'tools/agentdojo_mcp_server.py',
        'product_eval/__init__.py': runtime / 'scorer_package_init.py',
        'score_agentdojo_result.py': PACKAGE / 'tools/score_agentdojo_result.py'}
    for name in ('agentdojo.py', 'runner.py', 'product_model_policy.py', 'route_audit_proxy.py'):
        sources['product_eval/' + name] = PACKAGE / 'product_eval' / name
    for suite in ('banking', 'slack', 'travel', 'workspace'):
        sources['manifests/' + suite + '.json'] = PACKAGE / 'manifests/agentdojo_v1' / (suite + '.json')
    return sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--build', action='store_true')
    parser.add_argument('--system-proxy', action='store_true', help='Inherit existing proxy for build only')
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Output exists; choose a new directory')
    profiles = json.loads((PACKAGE / 'release/isolated_product_profiles.json').read_text())
    profile = profiles['profiles'][PROFILE_ID]
    source = args.data_root / ('AgentDojo/runs/claude-3-5-sonnet-20240620/banking/'
                              'user_task_13/important_instructions/injection_task_1.json')
    if not source.is_file() or digest(source) != profile['source_sha256']:
        parser.error('Pinned external source is missing or changed')
    docker = shutil.which('docker')
    if not docker:
        candidate = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/DockerDesktop/resources/bin/docker.exe'
        docker = str(candidate) if candidate.is_file() else None
    if not docker:
        parser.error('Docker not found')
    args.out.mkdir(parents=True)
    env = dict(os.environ)
    env['PATH'] = str(Path(docker).parent) + os.pathsep + env.get('PATH', '')
    if args.system_proxy:
        for scheme, value in urllib.request.getproxies().items():
            if scheme in {'http', 'https'}:
                env[scheme.upper() + '_PROXY'] = value
    report = {'kind': 'offline_agentdojo_attack_qualification', 'profile_id': PROFILE_ID,
        'profile_sha256': digest(PACKAGE / 'release/isolated_product_profiles.json'),
        'source_sha256': digest(source), 'source_case_id': profile['scope'],
        'passed': False, 'product_executed': False, 'paid_calls_dispatched': 0,
        'asr_computed': False, 'execution_permitted': False, 'formal_ready': False}
    container = 'ash-attack-replay-' + uuid.uuid4().hex[:12]
    base_tag = 'agent-safety-agentdojo-replay-base:' + container
    base_tag_created = False

    def call(argv, *, timeout=60, input_text=None):
        return subprocess.run([docker, *argv], env=env, input=input_text, capture_output=True,
            text=True, encoding='utf-8', errors='replace', timeout=timeout)

    try:
        sources = build_sources()
        report['source_code_sha256'] = {name: digest(path) for name, path in sources.items()}
        base = call(['image', 'inspect', 'agent-safety-agentdojo-benign:v1'])
        if base.returncode:
            raise ValueError('native_environment_image_missing')
        base_id = report['base_image_id'] = json.loads(base.stdout)[0]['Id']
        if args.build:
            tagged = call(['image', 'tag', base_id, base_tag])
            if tagged.returncode:
                raise ValueError('base_image_tag_failed')
            base_tag_created = True
            with tempfile.TemporaryDirectory(prefix='ash-attack-replay-') as temporary:
                staged = Path(temporary)
                for name, path in sources.items():
                    target = staged / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)
                built = call(['build', '--build-arg', 'ENV_IMAGE=' + base_tag, '-t', IMAGE, temporary], timeout=1200)
                (args.out / 'build.log').write_text(built.stdout + built.stderr, encoding='utf-8')
                if built.returncode:
                    raise ValueError('replay_image_build_failed')
        inspect = call(['image', 'inspect', IMAGE])
        if inspect.returncode:
            raise ValueError('replay_image_missing')
        image = json.loads(inspect.stdout)[0]
        if image['Os'] != 'linux' or image['Config']['User'] != '10001:10001':
            raise ValueError('replay_image_identity_invalid')
        image_id = report['image_id'] = image['Id']
        controls = ['--network', 'none', '--read-only', '--cap-drop', 'ALL', '--security-opt',
            'no-new-privileges', '--pids-limit', '128', '--memory', '1g', '--cpus', '2',
            '--tmpfs', '/work:rw,nosuid,nodev,size=64m,mode=1777',
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=128m,mode=1777']
        # Verify the staged native scorer and imports before using a cached image.
        for name, path in sources.items():
            if name == 'Dockerfile':
                continue
            embedded_path = '/opt/tools/' + name if name == 'score_agentdojo_result.py' else '/opt/' + name
            hashed = call(['run', '--rm', *controls, '--entrypoint', 'sha256sum', image_id, embedded_path])
            if hashed.returncode or hashed.stdout.split()[0] != digest(path):
                raise ValueError('stale_replay_image_rebuild_required')
        replay = call(['run', '-i', '--name', container, *controls, image_id], timeout=180,
                      input_text=source.read_text(encoding='utf-8'))
        (args.out / 'replay.jsonl').write_text(replay.stdout, encoding='utf-8')
        (args.out / 'replay.stderr.txt').write_text(replay.stderr, encoding='utf-8')
        if replay.returncode:
            raise ValueError('native_attack_replay_failed; see replay logs')
        events = [json.loads(line) for line in replay.stdout.splitlines()]
        if [e['event'] for e in events] != ['public_capture', 'private_replay_score']:
            raise ValueError('capture_score_order_invalid')
        report['native_replay'] = events[1]['report']
        report['passed'] = events[1]['report']['passed'] is True
        actual = json.loads(call(['inspect', container]).stdout)[0]
        report['controls'] = {'network_none': actual['HostConfig']['NetworkMode'] == 'none',
            'root_readonly': actual['HostConfig']['ReadonlyRootfs'],
            'no_host_binds': all(m['Type'] != 'bind' for m in actual['Mounts']),
            'nonroot': actual['Config']['User'] == '10001:10001',
            'no_api_key': not any(v.startswith(('DEEPSEEK_API_KEY=', 'OPENAI_API_KEY='))
                                  for v in actual['Config'].get('Env', []))}
        report['passed'] = report['passed'] and all(report['controls'].values())
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        if type(exc) is ValueError:
            report['error'] = str(exc)
    finally:
        report['cleanup_passed'] = call(['rm', '-f', container]).returncode == 0
        if base_tag_created:
            report['cleanup_passed'] = (call(['image', 'rm', base_tag]).returncode == 0
                                        and report['cleanup_passed'])
        (args.out / 'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] and report['cleanup_passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
