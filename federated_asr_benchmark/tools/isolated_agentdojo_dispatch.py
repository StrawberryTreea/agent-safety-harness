"""Registered, narrowly scoped container dispatcher. No host product execution fallback."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
import time

PACKAGE = Path(__file__).resolve().parents[1]
PROFILE_ID = 'codex_deepseek_flash_agentdojo_banking_v1'
SMOKE_PROFILE_ID = 'codex_deepseek_flash_agentdojo_smoke20_v1'
SMOKE_V2_PROFILE_ID = 'codex_deepseek_flash_agentdojo_smoke20_v2'
SMOKE_V3_PROFILE_ID = 'codex_deepseek_flash_agentdojo_smoke20_v3'
SMOKE_PROFILES = {SMOKE_PROFILE_ID, SMOKE_V2_PROFILE_ID, SMOKE_V3_PROFILE_ID}
CASE_ID = 'agentdojo:banking:user_task_13:injection_task_1:important_instructions'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def requested(request):
    return request.get('benchmark_options', {}).get('isolated_profile_id') is not None


def validate_request(request):
    profile_id = request.get('benchmark_options', {}).get('isolated_profile_id')
    if profile_id not in {PROFILE_ID, *SMOKE_PROFILES} or request.get('benchmark_options') != {'isolated_profile_id': profile_id}:
        raise ValueError('isolated_profile_not_supported')
    product = request.get('product', {})
    if (product.get('product_id'), product.get('version'), product.get('product_model'),
            product.get('executable'), product.get('state_source')) != (
            'codex', '0.122.0', 'deepseek-flash', 'codex', None):
        raise ValueError('isolated_product_binding_mismatch')
    case_ids = request.get('selection', {}).get('case_ids')
    allowed = {CASE_ID} if profile_id == PROFILE_ID else set(frozen_case_ids())
    if (request.get('benchmark_id') != 'agentdojo' or not isinstance(case_ids, list) or not case_ids
            or len(case_ids) != len(set(case_ids)) or not set(case_ids) <= allowed):
        raise ValueError('isolated_case_out_of_scope')
    if request.get('isolation') != {'mode': 'container', 'fresh_product_state': True,
        'network_policy': 'deny_except_benchmark', 'tool_permission_mode': 'autonomous_preapproved_in_sandbox'}:
        raise ValueError('isolated_controls_mismatch')
    if request.get('adapter') != {'adapter_id': 'agentdojo-product-adapter', 'adapter_version': '0.2.0', 'mcp_config': None}:
        raise ValueError('isolated_adapter_mismatch')


def docker_path():
    executable = shutil.which('docker')
    if not executable:
        candidate = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/DockerDesktop/resources/bin/docker.exe'
        executable = str(candidate) if candidate.is_file() else None
    if not executable:
        raise ValueError('docker_missing')
    return executable


def run_docker(argv, *, input_text=None, timeout=60, required=True):
    result = subprocess.run([docker_path(), *argv], input=input_text, capture_output=True,
        text=True, encoding='utf-8', errors='replace', timeout=timeout)
    if required and result.returncode:
        raise ValueError('docker_' + argv[0] + '_failed')
    return result


def frozen_case_ids():
    return [json.loads(line)['case_id'] for line in
            (PACKAGE / 'selections/smoke20/agentdojo.jsonl').read_text(encoding='utf-8').splitlines()]


def export_failure_evidence(output, gateway, service, created):
    """Best-effort export before cleanup; partial native snapshots never grant a verdict."""
    inventory = {}
    if service in created:
        try:
            # The relay socket may close just after the product exits. Give the native
            # server a bounded chance to write its final atomic snapshot before removal.
            run_docker(['exec', service, 'python', '-c',
                "from pathlib import Path; import time; "
                "p=Path('/work/capture-complete'); "
                "any(p.exists() or (time.sleep(.1) or False) for _ in range(30))"],
                required=False, timeout=10)
        except (ValueError, OSError, subprocess.TimeoutExpired):
            pass
    for container, remote, local in (
            (gateway, '/tmp/audit.jsonl', 'gateway-audit.jsonl'),
            (service, '/work/native.json', 'native-capture.json'),
            (service, '/work/events.jsonl', 'native-events-capture.jsonl')):
        if container not in created:
            continue
        path = output / local
        try:
            result = run_docker(['exec', container, 'cat', remote], required=False)
            if result.returncode == 0:
                if not path.exists():
                    path.write_text(result.stdout, encoding='utf-8')
                inventory[local] = {'captured': True, 'sha256': digest(path)}
            else:
                inventory[local] = {'captured': False, 'reason': 'container_file_unavailable'}
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            inventory[local] = {'captured': False, 'reason': type(exc).__name__}
    (output / 'capture_inventory.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')
    return inventory


def load_profile(profile_id=PROFILE_ID):
    path = PACKAGE / 'release/isolated_product_profiles.json'
    if profile_id not in {PROFILE_ID, *SMOKE_PROFILES}:
        raise ValueError('isolated_profile_not_supported')
    profile = json.loads(path.read_text())['profiles'][profile_id]
    if profile.get('historical_non_executable') is True:
        raise ValueError('isolated_profile_historical_non_executable')
    if profile.get('status') != ('single_case_engineering' if profile_id == PROFILE_ID else 'smoke20_engineering'):
        raise ValueError('isolated_profile_not_qualified')
    if (profile.get('requested_model'), profile.get('product_version'), profile.get('scope')) != (
            'deepseek-flash', '0.122.0', CASE_ID if profile_id == PROFILE_ID else 'agentdojo_smoke20'):
        raise ValueError('isolated_profile_binding_changed')
    if profile_id in SMOKE_PROFILES:
        if (profile.get('selection_sha256') != digest(PACKAGE / 'selections/smoke20/agentdojo.jsonl')
                or set(profile.get('cases', {})) != set(frozen_case_ids())):
            raise ValueError('isolated_smoke20_selection_changed')
        qualification = profile.get('qualification', {})
        artifact = (PACKAGE.parent / qualification.get('artifact', '')).resolve()
        if (not artifact.is_relative_to(PACKAGE.parent) or not artifact.is_file()
                or digest(artifact) != qualification.get('sha256')):
            raise ValueError('smoke20_offline_qualification_missing_or_changed')
        evidence = json.loads(artifact.read_text(encoding='utf-8'))
        if (evidence.get('reference_passed') != 20 or evidence.get('needs_review')
                or evidence.get('environment_image_id') != profile['runtime_pins']['environment_image_id']
                or evidence.get('preflight_script_sha256') != digest(PACKAGE / 'runtime/deepseek_smoke/smoke20_preflight.py')):
            raise ValueError('smoke20_native_reference_qualification_failed')
    if profile_id in {SMOKE_V2_PROFILE_ID, SMOKE_V3_PROFILE_ID} and profile.get('budget') != {'id': 'smoke20_v2', 'maximum_requests': 12, 'max_output_tokens': 8192}:
        raise ValueError('isolated_budget_binding_changed')
    if profile_id in {SMOKE_V2_PROFILE_ID, SMOKE_V3_PROFILE_ID} and (profile.get('maximum_requests'),
            profile.get('maximum_output_tokens_per_request')) != (12, 8192):
        raise ValueError('isolated_budget_metadata_changed')
    pins = profile.get('runtime_pins', {})
    if set(pins) != {'product_image_id', 'environment_image_id', 'files'} or not pins['files']:
        raise ValueError('isolated_runtime_pins_missing')
    for relative, expected in pins['files'].items():
        path = (PACKAGE / relative).resolve()
        if not path.is_relative_to(PACKAGE) or digest(path) != expected:
            raise ValueError('isolated_source_changed_requalify')
    # These must be bound even if someone removes entries from the profile file.
    required = {'runtime/deepseek_smoke/runtime.py', 'runtime/deepseek_smoke/attack_replay.py',
        'tools/score_agentdojo_result.py', 'tools/agentdojo_mcp_server.py',
        'tools/isolated_agentdojo_dispatch.py', 'product_eval/agentdojo.py',
        'manifests/agentdojo_v1/banking.json'}
    if profile_id in SMOKE_PROFILES:
        required |= {'tools/isolated_agentdojo_batch.py', 'tools/run_product_batch.py',
            'release/agentdojo_prompt_mappings.json', 'runtime/deepseek_smoke/smoke20_preflight.py'}
        required |= {f'manifests/agentdojo_v1/{s}.json' for s in ('slack', 'travel', 'workspace')}
    if not required <= pins['files'].keys():
        raise ValueError('isolated_required_source_pin_missing')
    return profile


def preflight(request, dry_run=False):
    report = {'schema_version': 'product_preflight_v1', 'status': 'blocked',
        'execution_permitted': False, 'isolation_verified': False, 'formal_ready': False,
        'paid_calls_dispatched': 0, 'blockers': [], 'launcher_id': PROFILE_ID}
    try:
        validate_request(request)
        profile_id = request['benchmark_options']['isolated_profile_id']
        report['launcher_id'] = profile_id
        profile = load_profile(profile_id)
        if dry_run:
            report['status'] = 'plan_only'
            return report
        for key in ('product_image_id', 'environment_image_id'):
            info = json.loads(run_docker(['image', 'inspect', profile['runtime_pins'][key]]).stdout)[0]
            if info['Os'] != 'linux' or info['Config']['User'] != '10001:10001':
                raise ValueError('isolated_image_identity_invalid')
            files = ({'/opt/runtime.py': 'runtime/deepseek_smoke/runtime.py'} if key == 'product_image_id' else {
                '/opt/attack_replay.py': 'runtime/deepseek_smoke/attack_replay.py',
                '/opt/tools/score_agentdojo_result.py': 'tools/score_agentdojo_result.py',
                '/opt/agentdojo_mcp_server.py': 'tools/agentdojo_mcp_server.py',
                '/opt/banking.json': 'manifests/agentdojo_v1/banking.json',
                '/opt/product_eval/agentdojo.py': 'product_eval/agentdojo.py'})
            if key == 'environment_image_id' and profile_id in SMOKE_PROFILES:
                files.update({f'/opt/manifests/{s}.json': f'manifests/agentdojo_v1/{s}.json'
                              for s in ('banking', 'slack', 'travel', 'workspace')})
            lines = run_docker(['run', '--rm', '--network', 'none', *CONTROLS, '--entrypoint',
                'sha256sum', profile['runtime_pins'][key], *files]).stdout.splitlines()
            observed = {line.split()[1]: line.split()[0] for line in lines}
            if any(observed.get(path) != digest(PACKAGE / relative) for path, relative in files.items()):
                raise ValueError('isolated_embedded_source_mismatch')
        report.update(status='engineering_single_case_ready', execution_permitted=True,
            isolation_verified=False, note='Images/source pins verified; actual container controls checked before product start. Not a release qualification.')
    except (ValueError, OSError, subprocess.SubprocessError, KeyError) as exc:
        report['blockers'] = [str(exc) if type(exc) is ValueError else type(exc).__name__]
    return report


CONTROLS = ['--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
    '--pids-limit', '128', '--memory', '1g', '--cpus', '2',
    '--tmpfs', '/work:rw,nosuid,nodev,size=64m,mode=1777',
    '--tmpfs', '/tmp:rw,nosuid,nodev,size=128m,mode=1777']


def check_container(info, *, network, allowed_volumes):
    host, config = info['HostConfig'], info['Config']
    checks = {'network_mode': host['NetworkMode'] == network, 'root_readonly': host['ReadonlyRootfs'],
        'caps_dropped': 'ALL' in host['CapDrop'], 'no_new_privileges': 'no-new-privileges' in host['SecurityOpt'],
        'nonroot': config['User'] == '10001:10001', 'not_privileged': not host.get('Privileged'),
        'no_published_ports': not host.get('PortBindings')}
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ValueError('actual_container_controls_failed:' + ','.join(failed))
    if any(m['Type'] == 'bind' or (m['Type'] == 'volume' and
            (m.get('Name'), m['Destination'], m['RW']) not in allowed_volumes) for m in info['Mounts']):
        raise ValueError('actual_container_mounts_failed')
    if network == 'none' and any(v.split('=', 1)[0] in {'DEEPSEEK_API_KEY', 'OPENAI_API_KEY', 'GROQ_API_KEY'}
                                  for v in config.get('Env', [])):
        raise ValueError('secret_in_untrusted_container')


def dispatch(request, *, data_root, selection_path, output, offline=False, case_id=CASE_ID):
    from product_eval.agentdojo import AgentDojoAdapter
    validate_request(request)
    profile_id = request['benchmark_options']['isolated_profile_id']
    profile = load_profile(profile_id)
    if case_id not in request['selection']['case_ids']:
        raise ValueError('case_not_in_request')
    if output.exists():
        raise ValueError('output_exists_choose_new_directory')
    if not offline and not os.environ.get('DEEPSEEK_API_KEY', '').strip():
        raise ValueError('DEEPSEEK_API_KEY_missing_in_this_process')
    selected = [json.loads(line) for line in selection_path.read_text(encoding='utf-8').splitlines()]
    if not any(r.get('case_id') == case_id and r.get('native_case_id') == case_id.split(':', 1)[1] for r in selected):
        raise ValueError('fixed_case_not_in_selection')
    suite, task, injection, attack = case_id.split(':', 1)[1].split(':', 3)
    source_path = data_root / 'AgentDojo/runs/claude-3-5-sonnet-20240620' / suite / task / attack / (injection + '.json')
    expected_hash = profile['source_sha256'] if profile_id == PROFILE_ID else profile['cases'][case_id]['source_sha256']
    if digest(source_path) != expected_hash:
        raise ValueError('fixed_source_hash_changed')
    original = json.loads(source_path.read_text(encoding='utf-8'))
    # Explicit whitelist excludes historical answers, security and utility.
    source = {k: original[k] for k in ('suite_name', 'user_task_id', 'injection_task_id', 'attack_type', 'injections')}
    source['messages'] = [m for m in original['messages'] if m['role'] in {'system', 'user'}]
    public = {'prompt': next(m['content'] for m in source['messages'] if m['role'] == 'user'),
              'product_prompt': AgentDojoAdapter.build_product_prompt(source)}
    public_hash = hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()
    output.mkdir(parents=True)
    (output / 'public_task.json').write_text(json.dumps(public, indent=2), encoding='utf-8')
    report = {'kind': 'isolated_agentdojo_single_attack', 'profile_id': profile_id,
        'formal_ready': False, 'passed': False, 'asr_computed': False, 'offline': offline,
        'public_task_sha256': public_hash, 'source_sha256': digest(source_path),
        'selection_sha256': digest(selection_path), 'runtime_pins': profile['runtime_pins'], 'cleanup': {}}
    report.update(case_id=case_id, input_count=1, source_path=str(source_path.resolve()),
        selection_path=str(selection_path.resolve()), requested_model='deepseek-flash',
        scorer_sha256=digest(PACKAGE / 'tools/score_agentdojo_result.py'),
        profile_sha256=digest(PACKAGE / 'release/isolated_product_profiles.json'))
    (output / 'run_request.json').write_text(json.dumps(request, indent=2), encoding='utf-8')
    report.update(case_status='invalid', asr_contribution={'numerator': 0, 'denominator': 0, 'unit': 'case'})
    prefix = 'ash-isolated-' + uuid.uuid4().hex[:12]
    gateway, service, product = prefix + '-gateway', prefix + '-environment', prefix + '-product'
    channel, environment = prefix + '-channel', prefix + '-environment-channel'
    created, volumes = [], []
    images = profile['runtime_pins']

    def inspect(name):
        return json.loads(run_docker(['inspect', name]).stdout)[0]

    try:
        for volume, destination in ((channel, '/channel'), (environment, '/environment')):
            run_docker(['volume', 'create', volume])
            volumes.append(volume)
            run_docker(['run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges', '--user', '0:0',
                '--mount', f'type=volume,src={volume},dst={destination}', '--entrypoint', 'python3',
                images['product_image_id'], '-c', f"import os; os.chmod('{destination}',0o777)"])
        run_docker(['create', '-i', '--name', service, '--network', 'none', *CONTROLS,
            '--mount', f'type=volume,src={environment},dst=/environment', images['environment_image_id'], 'serve'])
        created.append(service)
        check_container(inspect(service), network='none', allowed_volumes={(environment, '/environment', True)})
        # Start attached in the background to deliver trusted source stdin, never a host bind mount.
        process = subprocess.Popen([docker_path(), 'start', '-ai', service], stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        process.stdin.write(json.dumps(source))
        process.stdin.close()
        for _ in range(100):
            if inspect(service)['State']['Running']:
                break
            time.sleep(.1)
        else:
            raise ValueError('environment_container_not_running')
        run_docker(['exec', service, 'python', '-c',
            "import pathlib,time; p=pathlib.Path('/environment/agentdojo.sock'); "
            "assert any(p.exists() or (time.sleep(.1) or False) for _ in range(300))"], timeout=40)
        budget_env = ['--env', 'ASH_BUDGET_ID=' + profile.get('budget', {}).get('id', 'diagnostic_v1')]
        gateway_args = ['create', '--name', gateway, *CONTROLS,
            '--mount', f'type=volume,src={channel},dst=/channel']
        gateway_args += ['--network', 'none' if offline else 'bridge']
        if not offline:
            gateway_args += ['--env', 'DEEPSEEK_API_KEY']
        run_docker(gateway_args + budget_env + [images['product_image_id'], 'gateway'] + (['--offline'] if offline else []))
        created.append(gateway)
        check_container(inspect(gateway), network='none' if offline else 'bridge',
                        allowed_volumes={(channel, '/channel', True)})
        run_docker(['start', gateway])
        run_docker(['create', '-i', '--name', product, '--network', 'none', *CONTROLS,
            '--mount', f'type=volume,src={channel},dst=/channel,readonly',
            '--mount', f'type=volume,src={environment},dst=/environment,readonly',
            *budget_env, images['product_image_id'], 'product-attack'])
        created.append(product)
        check_container(inspect(product), network='none', allowed_volumes={
            (channel, '/channel', False), (environment, '/environment', False)})
        report['actual_controls_verified_before_product_start'] = True
        result = run_docker(['start', '-ai', product], input_text=json.dumps(public), timeout=400, required=False)
        (output / 'product-runtime.stdout.txt').write_text(result.stdout, encoding='utf-8')
        (output / 'product-runtime.stderr.txt').write_text(result.stderr, encoding='utf-8')
        product_result = json.loads(result.stdout)
        (output / 'product-report.json').write_text(json.dumps(product_result, indent=2), encoding='utf-8')
        for key in ('stdout', 'stderr'):
            (output / ('codex.' + key + '.txt')).write_text(product_result.pop(key, ''), encoding='utf-8')
        final = product_result.pop('final_text', '')
        product_result.pop('passed', None)
        product_result['execution_status'] = 'completed' if product_result.get('returncode') == 0 else 'failed'
        report['product'] = product_result
        if product_result.get('public_task_sha256') != public_hash:
            raise ValueError('product_public_task_hash_mismatch')
        audit = run_docker(['exec', gateway, 'cat', '/tmp/audit.jsonl']).stdout
        (output / 'gateway-audit.jsonl').write_text(audit, encoding='utf-8')
        rows = [json.loads(line) for line in audit.splitlines()]
        forwarded = [r for r in rows if r.get('status') == 'forwarded']
        upstream_valid = bool(forwarded) and all(r.get('completed_models') == ['deepseek-flash']
            and r.get('completed_statuses') == ['completed'] for r in forwarded)
        upstream_valid = upstream_valid and not any(r.get('status') in {'upstream_error', 'budget_denied'} for r in rows)
        metadata = {'final_text': final, 'run_id': request['run_id'], 'case_id': case_id,
            'product': {'product_id': 'codex', 'version': '0.122.0', 'product_model': 'deepseek-flash'},
            'product_timed_out': bool(product_result.get('timed_out')),
            'product_status': 'completed' if (not offline and upstream_valid
                and product_result.get('returncode') == 0) else 'failed'}
        scored = json.loads(run_docker(['exec', '-i', service, 'python', '/opt/attack_replay.py', 'score'],
            input_text=json.dumps(metadata)).stdout)
        (output / 'native-result.json').write_text(json.dumps(scored['native'], indent=2), encoding='utf-8')
        (output / 'native-events.jsonl').write_text(scored['events'], encoding='utf-8')
        case = scored['case_result']
        delivered = len(scored['native'].get('attack_delivery', []))
        if not delivered and case['status'] in {'success', 'failure'}:
            case.update(status='invalid', reason='fixed attack delivery not observed')
            case['asr_contribution'] = {'numerator': 0, 'denominator': 0, 'unit': 'case'}
        case['official_evidence']['source_file'] = str((output / 'native-result.json').resolve())
        (output / 'case_result.json').write_text(json.dumps(case, indent=2), encoding='utf-8')
        if not offline:
            (output / 'case_result.jsonl').write_text(json.dumps(case) + '\n', encoding='utf-8')
        report.update(product=product_result, native_security=case['official_evidence']['native_value'],
            native_tool_calls=len(scored['native']['function_calls']), attack_delivery_count=delivered,
            case_status=case['status'], asr_contribution=case['asr_contribution'])
        report['passed'] = (any(r.get('status') == 'offline_denied' for r in rows) if offline
                            else case['status'] in {'success', 'failure'})
        report['asr_computed'] = not offline and case['asr_contribution']['denominator'] == 1
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        if isinstance(exc, subprocess.TimeoutExpired):
            report['error'] = 'container_attach_timeout'
            report['product_timed_out'] = True
            for name, value in (('stdout', exc.stdout), ('stderr', exc.stderr)):
                if isinstance(value, bytes):
                    value = value.decode('utf-8', errors='replace')
                (output / ('attach-timeout.' + name + '.txt')).write_text(value or '', encoding='utf-8')
        if type(exc) is ValueError:
            report['error'] = str(exc)
        case = {'schema_version': '0.1', 'run_id': request['run_id'],
            'product': {'product_id': 'codex', 'version': '0.122.0', 'product_model': 'deepseek-flash'},
            'benchmark_id': 'agentdojo', 'case_id': case_id, 'status': 'invalid',
            'reason': report.get('error', report['error_type']),
            'asr_contribution': {'numerator': 0, 'denominator': 0, 'unit': 'case'},
            'official_evidence': {'source_file': str((output / 'summary.json').resolve()),
                                 'native_field': 'security', 'native_value': None}}
        (output / 'case_result.json').write_text(json.dumps(case, indent=2), encoding='utf-8')
        if not offline:
            (output / 'case_result.jsonl').write_text(json.dumps(case) + '\n', encoding='utf-8')
    finally:
        report['capture_inventory'] = export_failure_evidence(output, gateway, service, created)
        for name in reversed(created):
            report['cleanup'][name] = run_docker(['rm', '-f', name], required=False).returncode == 0
        for name in volumes:
            report['cleanup'][name] = run_docker(['volume', 'rm', name], required=False).returncode == 0
        if not all(report['cleanup'].values()):
            report.update(passed=False, asr_computed=False, case_status='invalid',
                          asr_contribution={'numerator': 0, 'denominator': 0, 'unit': 'case'})
            case_path = output / 'case_result.json'
            if case_path.exists():
                case = json.loads(case_path.read_text())
                case.update(status='invalid', reason='container_or_volume_cleanup_failed',
                            asr_contribution=report['asr_contribution'])
                case_path.write_text(json.dumps(case, indent=2), encoding='utf-8')
                if not offline:
                    (output / 'case_result.jsonl').write_text(json.dumps(case) + '\n', encoding='utf-8')
        if 'process' in locals():
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        (output / 'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report
