"""Local behavior checks. Private app modules are replaced by explicit doubles."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, mock_open, patch

from flask import Flask, g


CODE = Path(__file__).resolve().parents[1] / 'code'


def load_sample(name, dependencies=None):
    spec = importlib.util.spec_from_file_location(name, CODE / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, dependencies or {}):
        spec.loader.exec_module(module)
    return module


def dependency(name, **attributes):
    module = types.ModuleType(name)
    module.__dict__.update(attributes)
    return module


class APIContractTests(unittest.TestCase):
    def setUp(self):
        # Identity translator isolates the response contract from private i18n.
        self.api = load_sample('api_response', {
            'services': dependency('services'),
            'services.i18n_service': dependency(
                'services.i18n_service',
                translate_error=lambda code, fallback_message: fallback_message,
            ),
        })
        self.app = Flask(__name__)

    def test_success_preserves_status_and_request_id(self):
        with self.app.test_request_context():
            g.request_id = 'synthetic-request'
            response, status = self.api.api_ok({'task_id': 'demo-task'}, status=202)
            self.assertEqual(status, 202)
            self.assertEqual(response.get_json(), {
                'ok': True,
                'data': {'task_id': 'demo-task'},
                'request_id': 'synthetic-request',
            })

    def test_legacy_error_with_success_status_becomes_error(self):
        with self.app.test_request_context():
            response, status = self.api.normalize_legacy_result({
                'error': 'Invalid sample input', 'error_code': 'INVALID_INPUT',
                'field': 'topic',
            }, default_error_status=422)
            self.assertEqual(status, 422)
            self.assertEqual(response.get_json(), {
                'ok': False,
                'error': {
                    'code': 'INVALID_INPUT', 'message': 'Invalid sample input',
                    'details': {'field': 'topic'},
                },
            })

    def test_legacy_response_tuple_preserves_http_error(self):
        with self.app.test_request_context():
            response, status = self.api.normalize_legacy_result(
                ({'message': 'Missing sample'}, 404), default_error_code='NOT_FOUND',
            )
            self.assertEqual(status, 404)
            self.assertEqual(response.get_json()['error']['code'], 'NOT_FOUND')


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.module = load_sample('context_optimization')
        self.chat = self.module.ChatService()

    def test_deduplication_keeps_role_changes_and_nonconsecutive_repeats(self):
        history = [
            {'role': 'user', 'content': 'Hello  there'},
            {'role': 'user', 'content': ' HELLO there '},
            {'role': 'user', 'content': '   '},
            {'role': 'assistant', 'content': 'Hello there'},
            {'role': 'user', 'content': 'Hello there'},
        ]
        compacted = self.chat._compact_history(history)
        self.assertEqual(len(compacted), 3)
        self.assertEqual([m['role'] for m in compacted], ['user', 'assistant', 'user'])

    def test_latest_twelve_messages_keep_chronological_order(self):
        history = [{'role': 'user', 'content': f'message-{i:02}'} for i in range(20)]
        text = self.chat._build_history_text(history)
        self.assertNotIn('message-07', text)
        self.assertEqual(text.count('Estudiante:'), 12)
        self.assertLess(text.index('message-08'), text.index('message-19'))

    def test_budget_prioritizes_recent_content(self):
        history = [{'role': 'user', 'content': f'{i:02}-' + 'x' * 2000} for i in range(20)]
        text = self.chat._build_history_text(history)
        # Production accounting excludes separators and the section heading.
        message_lines = text.splitlines()[2:-1]
        self.assertLessEqual(sum(map(len, message_lines)), 9000)
        self.assertIn('19-', text)
        self.assertNotIn('08-', text)
        for line in message_lines:
            self.assertLessEqual(len(line.partition(': ')[2]), 1400)

    def test_assistant_items_have_separate_limit(self):
        text = self.chat._build_history_text([{'role': 'assistant', 'content': 'x' * 2000}])
        self.assertEqual(text.count('x'), 900)

    def test_template_is_read_once_per_class_cache(self):
        template = self.module.ConceptMapService
        fixture = mock_open(read_data='<html>synthetic template</html>')
        with patch('builtins.open', fixture):
            first = template._get_template()
            second = template._get_template()
        self.assertEqual(first, second)
        self.assertEqual(first, '<html>synthetic template</html>')
        fixture.assert_called_once()


class FacadeTests(unittest.TestCase):
    def test_import_does_not_initialize_service_and_first_access_is_lazy(self):
        facade = load_sample('ai_facade')
        self.assertIsNone(facade._ai_service_class)
        self.assertIsNone(facade._ai_service_instance)
        fake_instance = types.SimpleNamespace(model_name='synthetic-model')
        constructor = MagicMock(return_value=fake_instance)
        with patch.object(facade, '_build_ai_service_class', return_value=constructor):
            self.assertEqual(repr(facade.ai_service), '<LazyAIService proxy>')
            constructor.assert_not_called()
            self.assertEqual(facade.ai_service.model_name, 'synthetic-model')
            self.assertEqual(facade.ai_service.model_name, 'synthetic-model')
            constructor.assert_called_once_with()


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.db = types.SimpleNamespace(session=MagicMock())
        dependencies = {
            'database': dependency('database', db=self.db, GenerationJob=MagicMock()),
            'routes': dependency('routes'),
            'routes.api': dependency('routes.api'),
            'routes.api.generation': dependency(
                'routes.api.generation', _run_generation_task=MagicMock(),
                GENERATION_TASK_TTL_SECONDS=3600,
            ),
            'routes.api.pdf': dependency('routes.api.pdf', _run_pdf_task=MagicMock()),
            'routes.api.presentations': dependency(
                'routes.api.presentations', _run_presentation_task=MagicMock(),
            ),
            'routes.api.exam_simulators': dependency(
                'routes.api.exam_simulators', _run_exam_correction_task=MagicMock(),
                _run_exam_generation_task=MagicMock(),
            ),
        }
        self.worker = load_sample('generation_worker', dependencies)

    def test_failed_attempt_can_return_to_pending(self):
        job = types.SimpleNamespace(attempts=1, status='running')
        self.worker._retry_or_finish_with_error(job, {'max_attempts': 3}, 'Synthetic failure.')
        self.assertEqual(job.status, 'pending')
        self.assertIsNone(job.started_at)
        self.assertIsNone(job.finished_at)
        self.db.session.commit.assert_called_once()

    def test_final_failed_attempt_becomes_terminal(self):
        job = types.SimpleNamespace(attempts=3, status='running')
        self.worker._retry_or_finish_with_error(job, {'max_attempts': 3}, 'Synthetic failure.')
        self.assertEqual(job.status, 'error')
        self.assertIsNotNone(job.finished_at)
        self.db.session.commit.assert_called_once()

    def test_timeout_recovery_leaves_recent_jobs_running(self):
        from datetime import datetime, timedelta
        stale = types.SimpleNamespace(attempts=1, status='running',
                                      started_at=datetime.utcnow() - timedelta(seconds=700),
                                      get_request_payload=lambda: {'job_type': 'notes'})
        recent = types.SimpleNamespace(attempts=1, status='running',
                                       started_at=datetime.utcnow(),
                                       get_request_payload=lambda: {'job_type': 'notes'})
        self.worker.GenerationJob.query.filter_by.return_value.all.return_value = [stale, recent]
        limits = self.worker._resolve_limits(types.SimpleNamespace(config={}))
        self.worker._recover_timed_out_running_jobs(limits)
        self.assertEqual(stale.status, 'pending')
        self.assertEqual(recent.status, 'running')


if __name__ == '__main__':
    unittest.main()
