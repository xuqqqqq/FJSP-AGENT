"""Live, run-scoped model usage receipts. No prompt or response text is stored.

Providers own the counts: snapshots replace earlier counts for the same call.
Missing receipts stay unknown, including failed/cancelled calls. The web layer
tails this ledger; it never reconstructs usage from reasoning or tool logs.
"""
from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from typing import Any
import uuid

_RUN: ContextVar[dict | None] = ContextVar('model_usage_run', default=None)
_STAGE: ContextVar[dict] = ContextVar('model_usage_stage', default={})
_WRITE_LOCK = threading.Lock()
_READ_LOCK = threading.RLock()
_READERS: dict[str, 'UsageReader'] = {}
FIELDS = ('total_tokens', 'input_tokens', 'output_tokens', 'reasoning_tokens',
          'cache_read_tokens', 'cache_write_tokens')


def append_receipt(path: Path, event: dict) -> None:
    payload = (json.dumps({'timestamp': time.time(), **event}, ensure_ascii=False) + '\n').encode()
    # One O_APPEND write, shared with OpenCode's appendFileSync in subprocesses.
    with _WRITE_LOCK:
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            written = os.write(descriptor, payload)
            if written != len(payload):
                raise OSError('Incomplete model usage receipt write')
        finally:
            os.close(descriptor)


def _number(value: Any) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        return int(value)
    return None


def normalize_usage(raw: dict, backend: str) -> dict:
    if backend == 'opencode':
        cache = raw.get('cache') or {}
        # OpenCode's counters have their own convention. Preserve its reported
        # total; never add reasoning/cache to it a second time.
        values = [raw.get('total'), raw.get('input'), raw.get('output'),
                  raw.get('reasoning'), cache.get('read'), cache.get('write')]
    else:
        completion = raw.get('completion_tokens_details') or {}
        prompt = raw.get('prompt_tokens_details') or {}
        values = [raw.get('total_tokens'), raw.get('prompt_tokens', raw.get('input_tokens')),
                  raw.get('completion_tokens', raw.get('output_tokens')),
                  raw.get('reasoning_tokens', completion.get('reasoning_tokens')),
                  raw.get('prompt_cache_hit_tokens', raw.get('cached_tokens', prompt.get('cached_tokens'))),
                  raw.get('cache_creation_input_tokens')]
        # OpenAI-compatible completion counts include reasoning; cache is part
        # of input. Adding the two reported counters is exact, not estimation.
        if values[0] is None and _number(values[1]) is not None and _number(values[2]) is not None:
            values[0] = int(values[1]) + int(values[2])
    normalized = dict(zip(FIELDS, map(_number, values)))
    if normalized['total_tokens'] == 0 and any((normalized[field] or 0) > 0 for field in FIELDS[1:]):
        # Some clients initialize a missing total to zero. A zero total with
        # nonzero components is not a receipt proving zero consumption.
        normalized['total_tokens'] = None
    return normalized


def location_metadata(directory: Path | None) -> dict:
    parts = directory.parts if directory else ()
    round_index = next((int(match[1]) for part in parts if (match := re.fullmatch(r'round_(\d+)', part))), None)
    candidate = next((parts[i + 1] for i, part in enumerate(parts[:-1]) if part == 'candidates'), None)
    repair = next((int(match[1]) for part in parts if (match := re.fullmatch(r'repair_(\d+)', part))), None)
    return {'round_index': round_index, 'candidate_id': candidate, 'repair_index': repair,
            'phase': 'baseline_generation' if 'agent_generated_baseline' in parts else 'evolution' if round_index is not None else 'setup'}


def usage_stage(role: str):
    """Attribute all calls in one model adapter, including its internal retries."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            request = args[1] if len(args) > 1 else kwargs.get('request') or kwargs.get('spec')
            directory = kwargs.get('output_dir') or getattr(request, 'output_dir', None)
            suffix = str(kwargs.get('suffix') or '')
            actual_role = 'semantic_review' if 'semantic_review' in suffix else 'reflection' if 'reflection' in suffix else role
            stage = {**location_metadata(Path(directory) if directory else None), 'role': actual_role}
            token = _STAGE.set(stage)
            try:
                return function(*args, **kwargs)
            finally:
                _STAGE.reset(token)
        return wrapped
    return decorate


def _receipt_base() -> dict | None:
    scope = _RUN.get()
    return {**scope, **_STAGE.get()} if scope else None


def start_api_request(model: str) -> dict | None:
    receipt = _receipt_base()
    if receipt is not None:
        receipt.update(request_id='api:' + uuid.uuid4().hex, model=model, backend='api')
        api_receipt(receipt, 'request_started')
    return receipt


def api_receipt(receipt: dict | None, event_type: str, *, usage=None, outcome=None) -> None:
    if receipt is None:
        return
    event = {k: v for k, v in receipt.items() if k != 'path'}
    event['type'] = event_type
    if isinstance(usage, dict):
        event['usage'] = usage
    if outcome is not None:
        event['outcome'] = outcome
    append_receipt(receipt['path'], event)


def opencode_usage_environment(model: str | None) -> dict[str, str]:
    scope = _receipt_base()
    if scope is None:
        return {}
    invocation = uuid.uuid4().hex
    metadata = {k: v for k, v in scope.items() if k != 'path'}
    metadata.update(invocation_id=invocation, backend='opencode', model=model)
    append_receipt(scope['path'], {'type': 'invocation_started', **metadata})
    return {'ALGOFORGE_USAGE_EVENTS_PATH': str(scope['path']),
            'ALGOFORGE_USAGE_METADATA': json.dumps(metadata, ensure_ascii=False)}


def finish_opencode_usage(environment: dict, outcome: str) -> None:
    path = environment.get('ALGOFORGE_USAGE_EVENTS_PATH')
    metadata = environment.get('ALGOFORGE_USAGE_METADATA')
    if path and metadata:
        append_receipt(Path(path), {'type': 'invocation_finished', **json.loads(metadata), 'outcome': outcome})


class UsageReader:
    """Incrementally consume provider receipts, preserving an incomplete last line."""
    def __init__(self, path: Path):
        self.path = path
        self.identity = None
        self.offset = 0
        self.pending = b''
        self.calls: dict[str, dict] = {}
        self.invocations: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}
        self.invalid_receipts = 0
        self.updated_at = None

    def read(self) -> dict:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return {'available': False, 'recorded_total_tokens': None, 'total_tokens': None,
                    'completeness': 'not_recorded', 'by_role': [], 'by_round': [], 'by_candidate': [], 'recent_calls': []}
        identity = (stat.st_dev, stat.st_ino)
        if self.identity != identity or stat.st_size < self.offset:
            self.__init__(self.path)
            self.identity = identity
        with self.path.open('rb') as stream:
            stream.seek(self.offset)
            data = stream.read()
            self.offset += len(data)
        if data:
            lines = (self.pending + data).split(b'\n')
            self.pending = lines.pop()
            for line in lines:
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    self.invalid_receipts += 1
                    continue
                if isinstance(event, dict):
                    self.apply(event)
        return self.snapshot()

    def apply(self, event: dict) -> None:
        kind = event.get('type')
        self.updated_at = event.get('timestamp', self.updated_at)
        run_id = event.get('run_id')
        if kind in ('run_started', 'run_finished') and run_id:
            self.runs.setdefault(run_id, {}).update(event)
            return
        invocation = event.get('invocation_id')
        if kind in ('invocation_started', 'invocation_finished') and invocation:
            self.invocations.setdefault(invocation, {}).update(event)
            return
        request_id = event.get('request_id')
        if not request_id:
            return
        call = self.calls.setdefault(request_id, {'request_id': request_id})
        origin = {'run_id', 'round_index', 'candidate_id', 'repair_index', 'phase', 'invocation_id'}
        call.update({k: v for k, v in event.items() if k not in ('usage', 'type') and not (k in origin and k in call)})
        call.setdefault('started_at', event.get('timestamp'))
        if kind == 'request_finished':
            call['finished'] = True
        if isinstance(event.get('usage'), dict):
            raw = call.setdefault('raw_usage', {})
            for key, value in event['usage'].items():
                if isinstance(value, dict) and isinstance(raw.get(key), dict):
                    raw[key].update(value)
                else:
                    raw[key] = value
            call.update(normalize_usage(raw, call.get('backend', 'api')))

    def call_active(self, call: dict) -> bool:
        if call.get('finished') or self.runs.get(call.get('run_id'), {}).get('type') == 'run_finished':
            return False
        invocation = self.invocations.get(call.get('invocation_id'), {})
        return invocation.get('type') != 'invocation_finished'

    def summarize(self, calls: list[dict]) -> dict:
        totals = {field: sum(values) if (values := [c[field] for c in calls if c.get(field) is not None]) else None for field in FIELDS}
        missing = [c for c in calls if c.get('total_tokens') is None]
        active = sum(self.call_active(c) for c in calls)
        return {**totals, 'total_tokens': totals['total_tokens'] if not missing and not active else None,
                'recorded_total_tokens': totals['total_tokens'],
                'call_count': len(calls), 'metered_calls': len(calls) - len(missing),
                'active_calls': active, 'missing_usage_calls': sum(not self.call_active(c) for c in missing),
                'pending_usage_calls': sum(self.call_active(c) for c in missing)}

    def snapshot(self) -> dict:
        calls = list(self.calls.values())
        summary = self.summarize(calls)
        known_invocations = {c.get('invocation_id') for c in calls}
        untracked = sum(i not in known_invocations for i in self.invocations)
        active_invocations = sum(v.get('type') != 'invocation_finished' and self.runs.get(v.get('run_id'), {}).get('type') != 'run_finished' for v in self.invocations.values())
        partial = summary['missing_usage_calls'] > 0 or untracked > 0 or self.invalid_receipts > 0
        pending = summary['active_calls'] > 0 or active_invocations > 0
        summary.update(available=True, schema_version=1, updated_at=self.updated_at,
                       source='provider_usage', active_invocations=active_invocations,
                       untracked_invocations=untracked, invalid_receipts=self.invalid_receipts,
                       completeness='pending' if pending and not partial else 'partial' if partial else 'complete' if calls else 'no_calls')
        summary['total_tokens'] = summary['recorded_total_tokens'] if not partial and not pending else None
        for name, key in [('by_role', 'role'), ('by_round', 'round_index'), ('by_candidate', 'candidate_id'), ('by_run', 'run_id')]:
            groups: dict[tuple, list] = {}
            for call in calls:
                value = call.get(key) if key != 'role' else call.get(key, 'other')
                group = (call.get('round_index'), value) if key == 'candidate_id' else (value,)
                groups.setdefault(group, []).append(call)
            summary[name] = [
                {key: group[-1], **({'round_index': group[0]} if key == 'candidate_id' else {}), **self.summarize(items)}
                for group, items in groups.items()
            ]
        summary['recent_calls'] = [
            {**{k: v for k, v in call.items() if k not in ('raw_usage', 'path')}, 'active': self.call_active(call)}
            for call in sorted(calls, key=lambda x: x.get('started_at') or 0)[-24:]
        ]
        return summary


def read_usage(path: Path) -> dict:
    path = path.resolve()
    with _READ_LOCK:
        return _READERS.setdefault(str(path), UsageReader(path)).read()


def current_usage() -> dict:
    scope = _RUN.get()
    return read_usage(scope['path']) if scope else {}


def record_usage_run(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if _RUN.get() is not None:
            return function(*args, **kwargs)
        request = args[0] if args else kwargs.get('request')
        directory = Path(kwargs.get('output_dir') or request.output_dir).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        scope = {'path': directory / 'run_usage.jsonl', 'run_id': uuid.uuid4().hex}
        token = _RUN.set(scope)
        append_receipt(scope['path'], {'type': 'run_started', 'run_id': scope['run_id']})
        result = None
        try:
            result = function(*args, **kwargs)
            return result
        finally:
            try:
                append_receipt(scope['path'], {'type': 'run_finished', 'run_id': scope['run_id']})
                snapshot = read_usage(scope['path'])
            finally:
                _RUN.reset(token)
            summary_path = directory / 'run_usage_summary.json'
            summary_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            if isinstance(result, dict):
                result['token_usage'] = snapshot
                by_round = {item['round_index']: item for item in snapshot['by_round']}
                for item in result.get('rounds', []):
                    item['token_usage'] = by_round.get(item.get('round_index'), {})
                result.setdefault('artifacts', {}).update(token_usage=str(summary_path), token_usage_events=str(scope['path']))
            # Reports are persisted before this outer finally, including failed
            # and interrupted runs. Finalize their receipt status as well.
            for name in ('standard_worker_loop_manifest.json', 'loop_result.json'):
                path = directory / name
                if path.exists():
                    payload = json.loads(path.read_text(encoding='utf-8'))
                    payload['token_usage'] = snapshot
                    by_round = {item['round_index']: item for item in snapshot['by_round']}
                    for item in payload.get('rounds', []):
                        item['token_usage'] = by_round.get(item.get('round_index'), {})
                    payload.setdefault('artifacts', {}).update(token_usage=str(summary_path), token_usage_events=str(scope['path']))
                    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return wrapped
