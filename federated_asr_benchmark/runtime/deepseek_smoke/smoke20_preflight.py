"""Offline native reference checks for frozen smoke20 public tasks; no product/model."""
import hashlib
import importlib.metadata
import json
import sys
import calendar
import re
import ast
import inspect
import textwrap
from decimal import Decimal

sys.path.insert(0, '/opt')


def reference_arithmetic(prompt, values):
    """Optional arithmetic from public transaction observations, never an oracle answer."""
    if 'total spending' not in prompt.lower():
        return ''
    match = re.search(r'\bin\s+([A-Za-z]+)\s+(\d{4})\b', prompt)
    months = {name.lower(): index for index, name in enumerate(calendar.month_name) if name}
    if not match or match[1].lower() not in months:
        return ''
    prefix = f'{match[2]}-{months[match[1].lower()]:02d}'
    transactions = []
    for value in values:
        if isinstance(value, list):
            for row in value:
                row = row.model_dump(mode='json') if hasattr(row, 'model_dump') else row
                if isinstance(row, dict) and {'sender', 'date', 'amount'} <= row.keys():
                    transactions.append(row)
    if not transactions:
        return ''
    total = sum((Decimal(str(row['amount'])) for row in transactions
                 if row['sender'] == 'me' and str(row['date']).startswith(prefix)), Decimal('0'))
    return f'Total spending in {match[1]} {match[2]}: {total}'


def travel_arithmetic(prompt, records):
    """Compute requested totals only from observed prices and the public duration."""
    if 'car' in prompt.lower():
        days = re.search(r'for (\d+) days', prompt)
        duration = int(days[1]) if days else (7 if 'week' in prompt.lower() else None)
        if duration:
            return '\n'.join(f'{company}: total for {duration} days = {Decimal(str(price)) * duration}'
                for function, value in records if function == 'get_car_price_per_day'
                for company, price in value.items())
    days = re.search(r'hotel for (\d+) days', prompt)
    meals = re.search(r'for (\d+) per day at the restaurant', prompt)
    if not days or not meals:
        return ''
    prices, ratings, restaurant = {}, {}, {}
    for function, value in records:
        if function == 'get_hotels_prices':
            prices.update(value)
        elif function == 'get_rating_reviews_for_hotels':
            ratings.update({name: float(re.search(r'Rating: ([\d.]+)', text)[1]) for name, text in value.items()})
        elif function == 'get_price_for_restaurants':
            restaurant.update(value)
    if not prices or not ratings or not restaurant:
        return ''
    hotel = max(ratings, key=ratings.get)
    hotel_min = Decimal(re.search(r'Price range: ([\d.]+)', prices[hotel])[1])
    name, meal_price = next(iter(restaurant.items()))
    total = (hotel_min + Decimal(str(meal_price)) * int(meals[1])) * int(days[1])
    return f'{hotel}; {name}; minimum total cost = {total}'


def class_hash_without_prompt(task):
    node = next(n for n in ast.parse(textwrap.dedent(inspect.getsource(type(task)))).body if isinstance(n, ast.ClassDef))
    node.body = [n for n in node.body if not (isinstance(n, (ast.Assign, ast.AnnAssign)) and any(
        isinstance(t, ast.Name) and t.id == 'PROMPT' for t in (n.targets if isinstance(n, ast.Assign) else [n.target])))]
    return hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()


def main():
    from agentdojo.task_suite.load_suites import get_suite
    from agentdojo.types import text_content_block_from_string
    import agentdojo_mcp_server as native
    request = json.load(sys.stdin)
    if importlib.metadata.version('agentdojo') != '0.1.35':
        raise ValueError('package_version_mismatch')
    for row in request['cases']:
        source = row['source']
        suite = get_suite('v1', source['suite_name'])
        task = suite.get_user_task_by_id(source['user_task_id'])
        public = {'case_id': row['case_id'], 'suite': source['suite_name'],
                  'user_task_id': source['user_task_id'], 'prompt': task.PROMPT}
        print(json.dumps({'event': 'public_capture', 'public_task': public,
            'sha256': hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()}), flush=True)
        report = {'case_id': row['case_id'], 'passed': False, 'product_executed': False}
        stage = 'manifest_check'
        try:
            manifest = request['manifests'][source['suite_name']]
            if {t['name'] for t in manifest['tools']} != {t.name for t in suite.tools}:
                report['manifest_missing_tools'] = sorted({t.name for t in suite.tools} - {t['name'] for t in manifest['tools']})
                report['manifest_extra_tools'] = sorted({t['name'] for t in manifest['tools']} - {t.name for t in suite.tools})
                raise ValueError('native_tool_set_mismatch')
            stage = 'prompt_check'
            source_prompt = source['messages'][0]['content']
            report['prompt_comparison'] = 'exact' if source_prompt == task.PROMPT else 'whitespace_only'
            if ' '.join(source_prompt.split()) != ' '.join(task.PROMPT.split()):
                report['prompt_comparison'] = 'wording_change'
                report['source_prompt_sha256'] = hashlib.sha256(source['messages'][0]['content'].encode()).hexdigest()
                report['runtime_prompt_sha256'] = hashlib.sha256(task.PROMPT.encode()).hexdigest()
                mapping = request.get('prompt_mappings', {}).get(row['case_id'], {})
                if (mapping.get('source_prompt_sha256') != report['source_prompt_sha256']
                        or mapping.get('runtime_prompt_sha256') != report['runtime_prompt_sha256']
                        or mapping.get('native_class_sha256_without_prompt') != class_hash_without_prompt(task)):
                    raise ValueError('native_public_prompt_mismatch')
                report['prompt_comparison'] = 'reviewed_task_id_mapping_preserve_source'
            # Only the native public user task and empty injections enter this reference control.
            clean = {**source, 'injections': {}}
            suite, task, runtime, pre, post = native._load_runtime(clean, 'v1')
            stage = 'native_reference_replay'
            calls, errors, observations, values, records = [], [], [], [], []
            for call in task.ground_truth(pre):
                value, error = runtime.run_function(post, call.function, call.args)
                calls.append(call)
                observations.append(native._format_tool_result(value))
                values.append(value)
                records.append((call.function, value))
                if error:
                    errors.append(type(error).__name__)
            def utility(before, after, trace, final):
                return suite._check_user_task_utility(task, [text_content_block_from_string(final)], before, after, trace)
            stage = 'native_utility_check'
            computed = reference_arithmetic(source_prompt, values) or travel_arithmetic(task.PROMPT, records)
            reference = utility(pre, post, calls, '\n'.join(observations + ([computed] if computed else [])))
            no_action = utility(pre, pre, [], '')
            report.update(reference_utility=reference, no_action_utility=no_action,
                reference_final_text_source=('native_tool_observations_and_public_arithmetic' if computed
                                             else 'native_tool_observations_only'),
                tool_count=len(suite.tools), reference_tool_calls=len(calls), tool_errors=errors,
                passed=reference is True and no_action is False and not errors)
            if not report['passed']:
                report['review_reason'] = 'reference_or_negative_control_not_discriminating'
        except Exception as exc:
            report['error_type'] = type(exc).__name__
            report['failure_stage'] = stage
            if type(exc) is ValueError and str(exc) in {'native_tool_set_mismatch', 'native_public_prompt_mismatch'}:
                report['review_reason'] = str(exc)
        print(json.dumps({'event': 'private_reference_score', 'report': report}), flush=True)


if __name__ == '__main__':
    main()
