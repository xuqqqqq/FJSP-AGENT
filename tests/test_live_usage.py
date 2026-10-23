from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import json
import io
import os
import shutil
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error

from harness_agent.deepseek_client import DeepSeekClient, DeepSeekConfig
from harness_agent.usage import (
    UsageReader, append_receipt, api_receipt, current_usage, finish_opencode_usage,
    normalize_usage, opencode_usage_environment, read_usage, record_usage_run,
    start_api_request, usage_stage,
)
from harness_agent.web.server import job_token_usage, public_job
from harness_agent.agents.opencode_main import OpenCodeMainAgent
from harness_agent.workers.opencode_worker import opencode_subprocess_environment


class LiveUsageTests(unittest.TestCase):
    def test_provider_conventions_never_double_count_reasoning_or_cache(self):
        api = normalize_usage({'prompt_tokens': 100, 'completion_tokens': 30,
            'completion_tokens_details': {'reasoning_tokens': 20},
            'prompt_tokens_details': {'cached_tokens': 60}}, 'api')
        self.assertEqual(130, api['total_tokens'])
        self.assertEqual(20, api['reasoning_tokens'])
        code = normalize_usage({'total': 123, 'input': 80, 'output': 30,
            'reasoning': 10, 'cache': {'read': 50}}, 'opencode')
        self.assertEqual(123, code['total_tokens'])
        self.assertIsNone(normalize_usage({'input': 80}, 'opencode')['total_tokens'])
        self.assertIsNone(normalize_usage({'total': 0, 'input': 80}, 'opencode')['total_tokens'])

    def test_compatible_provider_explicitly_requests_stream_usage(self):
        configuration = {'provider': {'local': {'npm': '@ai-sdk/openai-compatible', 'options': {'timeout': 12345, 'includeUsage': False}}}}
        with patch.dict(os.environ, {'OPENCODE_CONFIG_CONTENT': json.dumps(configuration)}), patch('harness_agent.workers.opencode_worker.load_local_env'):
            environment = opencode_subprocess_environment(runtime_config={'agent': {}})
        provider = json.loads(environment['OPENCODE_CONFIG_CONTENT'])['provider']['local']
        self.assertTrue(provider['options']['includeUsage'])
        self.assertEqual(12345, provider['options']['timeout'])

    def test_incremental_snapshots_and_partial_line_are_not_added_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'usage.jsonl'
            append_receipt(path, {'type': 'run_started', 'run_id': 'r'})
            event = {'type': 'request_usage', 'request_id': 'm', 'run_id': 'r', 'backend': 'api', 'usage': {'total_tokens': 10}}
            append_receipt(path, event)
            reader = UsageReader(path)
            self.assertEqual(10, reader.read()['recorded_total_tokens'])
            append_receipt(path, event)
            event['usage']['total_tokens'] = 15
            payload = (json.dumps(event) + '\n').encode()
            with path.open('ab') as stream:
                stream.write(payload[:-2])
            self.assertEqual(10, reader.read()['recorded_total_tokens'])
            with path.open('ab') as stream:
                stream.write(payload[-2:])
            self.assertEqual(15, reader.read()['recorded_total_tokens'])
            self.assertEqual(15, reader.read()['recorded_total_tokens'])
            self.assertEqual(1, reader.read()['call_count'])

    def test_failed_invocation_without_message_is_unknown_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            @record_usage_run
            def run(*, output_dir):
                env = opencode_usage_environment('model')
                finish_opencode_usage(env, 'timeout')
            run(output_dir=Path(tmp))
            usage = read_usage(Path(tmp) / 'run_usage.jsonl')
            self.assertEqual(1, usage['untracked_invocations'])
            self.assertEqual('partial', usage['completeness'])
            self.assertIsNone(usage['recorded_total_tokens'])
            self.assertIsNone(usage['total_tokens'])

    def test_replayed_message_keeps_original_round_and_is_not_charged_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'usage.jsonl'
            event = {'type': 'request_usage', 'request_id': 'same-message', 'run_id': 'first', 'round_index': 0,
                     'backend': 'opencode', 'usage': {'total': 15}}
            append_receipt(path, event)
            append_receipt(path, dict(event, run_id='resumed', round_index=1))
            summary = read_usage(path)
            self.assertEqual(15, summary['recorded_total_tokens'])
            self.assertEqual(1, summary['call_count'])
            self.assertEqual(0, summary['by_round'][0]['round_index'])

    def test_parallel_stage_attribution_and_resume_keep_all_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            class Adapter:
                @usage_stage('coding_worker')
                def call(self, request):
                    receipt = start_api_request('model')
                    api_receipt(receipt, 'request_usage', usage={'total_tokens': 10})
                    api_receipt(receipt, 'request_finished', outcome='response_received')

            @record_usage_run
            def run(*, output_dir):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [executor.submit(copy_context().run, Adapter().call,
                        SimpleNamespace(output_dir=output_dir / 'worker_loop/round_000/candidates' / str(i) / 'worker')) for i in range(2)]
                    for future in futures:
                        future.result()
                return {'rounds': [{'round_index': 0}]}

            first = run(output_dir=Path(tmp))
            self.assertEqual(20, first['token_usage']['recorded_total_tokens'])
            second = run(output_dir=Path(tmp))
            self.assertEqual(40, second['token_usage']['recorded_total_tokens'])
            self.assertEqual(40, second['rounds'][0]['token_usage']['recorded_total_tokens'])
            self.assertEqual(2, len(second['token_usage']['by_run']))
            self.assertEqual(2, len(second['token_usage']['by_candidate']))

    def test_invalid_model_response_still_keeps_provider_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            @record_usage_run
            def run(*, output_dir):
                client = DeepSeekClient(DeepSeekConfig(api_key='private-key'))
                client.chat([{'role': 'user', 'content': 'private prompt'}])
            raw = json.dumps({'choices': [], 'usage': {'prompt_tokens': 100, 'completion_tokens': 4, 'total_tokens': 104}})
            with patch('harness_agent.deepseek_client.urlopen_read_with_deadline', return_value=raw):
                with self.assertRaisesRegex(RuntimeError, 'no choices'):
                    run(output_dir=Path(tmp))
            usage = read_usage(Path(tmp) / 'run_usage.jsonl')
            self.assertEqual(104, usage['recorded_total_tokens'])
            self.assertNotIn('private-key', (Path(tmp) / 'run_usage.jsonl').read_text())
            self.assertNotIn('private prompt', (Path(tmp) / 'run_usage.jsonl').read_text())

    def test_transport_retry_is_an_independent_call_not_erased(self):
        with tempfile.TemporaryDirectory() as tmp:
            @record_usage_run
            def run(*, output_dir):
                return DeepSeekClient(DeepSeekConfig(api_key='key')).chat([{'role': 'user', 'content': 'hello'}])
            response = json.dumps({'choices': [{'message': {'content': 'ok'}}], 'usage': {'total_tokens': 12}})
            with patch('harness_agent.deepseek_client.urlopen_read_with_deadline', side_effect=[urllib.error.URLError('lost connection'), response]), patch('harness_agent.deepseek_client.time.sleep'):
                run(output_dir=Path(tmp))
            usage = read_usage(Path(tmp) / 'run_usage.jsonl')
            self.assertEqual(2, usage['call_count'])
            self.assertEqual(12, usage['recorded_total_tokens'])
            self.assertEqual(1, usage['missing_usage_calls'])
            self.assertIsNone(usage['total_tokens'])
            self.assertIsNone(usage['by_role'][0]['total_tokens'])
            self.assertFalse(usage['recent_calls'][0]['active'])

    def test_web_sees_stream_usage_before_response_finishes_without_log_scan(self):
        received, release = threading.Event(), threading.Event()
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def __iter__(self):
                yield b'data: {"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12},"choices":[]}\n'
                received.set()
                release.wait(3)
                yield b'data: {"choices":[{"delta":{"content":"ok"}}]}\n'
                yield b'data: [DONE]\n'

        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            directory = job_dir / 'run/standard_worker_loop'
            errors = []
            @record_usage_run
            def run(*, output_dir):
                DeepSeekClient(DeepSeekConfig(api_key='key')).chat_with_usage([{'role': 'user', 'content': 'hello'}], stream=True)
            def target():
                try: run(output_dir=directory)
                except Exception as exc: errors.append(exc)
            with patch('urllib.request.urlopen', return_value=Response()):
                thread = threading.Thread(target=target)
                thread.start()
                try:
                    self.assertTrue(received.wait(3))
                    job = {'id': 'live', 'title': 'test', 'status': 'running', 'created_at': '', 'updated_at': '', 'job_dir': str(job_dir)}
                    usage = public_job(job)['token_usage']
                    self.assertEqual(12, usage['recorded_total_tokens'])
                    self.assertEqual(1, usage['active_calls'])
                    self.assertEqual('pending', usage['completeness'])
                finally:
                    release.set()
                    thread.join(4)
            self.assertFalse(errors)
            self.assertEqual('complete', job_token_usage(job)['completeness'])
            self.assertEqual(12, job_token_usage(job)['total_tokens'])

    def test_old_job_has_no_invented_zero_total(self):
        usage = job_token_usage({'job_dir': '/nonexistent-old-job'})
        self.assertFalse(usage['available'])
        self.assertIsNone(usage['recorded_total_tokens'])

    @unittest.skipUnless(os.name != 'nt' and shutil.which('node'), 'Requires a local Node executable')
    def test_opencode_adapter_passes_scope_into_real_plugin_subprocess(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / 'fake-opencode.mjs'
            executable.write_text('''#!/usr/bin/env node
const config = JSON.parse(process.env.OPENCODE_CONFIG_CONTENT);
const {ReasoningCapture} = await import(config.plugin[0]);
const hooks = await ReasoningCapture();
await hooks.event({event: {type: "message.updated", properties: {info: {
  id: "receipt", sessionID: "isolated", role: "assistant", agent: "algoforge-main",
  modelID: "test-model", finish: "stop", tokens: {total: 137, input: 120, output: 17}
}}}});
await hooks.dispose();
console.log(JSON.stringify({type: "text", sessionID: "isolated", timestamp: Date.now(), part: {type: "text", text: "{}"}}));
''')
            executable.chmod(0o755)
            @record_usage_run
            def run(*, output_dir):
                target = output_dir / 'worker_loop/round_001/main_agent'
                target.mkdir(parents=True)
                agent = OpenCodeMainAgent(executable=str(executable), model='test-model', project_root=root, max_subagents=0)
                agent._run_once(output_dir=target, attachments=[], prompt='offline fixture', suffix='_semantic_review', timeout_seconds=5)
            run(output_dir=root / 'run')
            usage = read_usage(root / 'run/run_usage.jsonl')
            self.assertEqual(137, usage['recorded_total_tokens'])
            self.assertEqual('complete', usage['completeness'])
            self.assertEqual('semantic_review', usage['by_role'][0]['role'])
            self.assertEqual(1, usage['by_round'][0]['round_index'])
            self.assertEqual(0, usage['untracked_invocations'])

    def test_error_responses_with_usage_are_still_metered(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def __iter__(self):
                yield b'data: {"error":{"message":"bad request"},"usage":{"total_tokens":7}}\n'
        with tempfile.TemporaryDirectory() as tmp:
            @record_usage_run
            def run(*, output_dir, stream):
                DeepSeekClient(DeepSeekConfig(api_key='key')).chat_with_usage([{'role': 'user', 'content': 'hello'}], stream=stream)
            with patch('urllib.request.urlopen', return_value=Response()):
                with self.assertRaisesRegex(RuntimeError, 'stream error'):
                    run(output_dir=Path(tmp), stream=True)
            error = urllib.error.HTTPError('http://test', 400, 'bad', {}, io.BytesIO(b'{"usage":{"total_tokens":9}}'))
            with patch('harness_agent.deepseek_client.urlopen_read_with_deadline', side_effect=error):
                with self.assertRaisesRegex(RuntimeError, 'HTTP 400'):
                    run(output_dir=Path(tmp), stream=False)
            usage = read_usage(Path(tmp) / 'run_usage.jsonl')
            self.assertEqual(16, usage['recorded_total_tokens'])
            self.assertEqual(2, usage['metered_calls'])


if __name__ == '__main__':
    unittest.main()
