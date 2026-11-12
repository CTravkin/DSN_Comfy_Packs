import asyncio
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class TextTests(unittest.TestCase):
    def test_catalog_maps_readable_names_to_server_ids(self):
        from Text.config import load_config

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'text.json'
            path.write_text(json.dumps({
                'prompts': 'templates',
                'models': {'gemma': 'Gemma для отчетов'},
                'servers': {'local': {'name': 'Локальный', 'url': 'http://127.0.0.1:9931/v1',
                                      'models': {'gemma': 'Gemma Local'}, 'temperature': 0.2}},
            }), encoding='utf-8')
            config = load_config(path)
            server = config.servers['Локальный']
            self.assertEqual(server.models, {'Gemma для отчетов': 'Gemma Local'})
            self.assertEqual(server.temperature, 0.2)
            self.assertEqual(config.prompts, Path(tmp).resolve() / 'templates')

    def test_materials_keep_file_order_and_reject_folders_and_escape(self):
        from Text.files import collect_text, resolve_path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'table.csv').write_text('имя,сумма\nтест,1', encoding='utf-8-sig')
            (root / 'note.md').write_text('Заметка', encoding='utf-8')
            (root / 'other.pdf').touch()
            (root / 'work').mkdir()
            self.assertEqual(resolve_path(root / 'work', str(root / 'note.md')), (root / 'note.md').resolve())
            self.assertEqual(collect_text(root, ['table.csv', str(root / 'note.md')], 'Транскрипт'),
                             'Транскрипт\n\n## table.csv\n\nимя,сумма\nтест,1\n\n## note.md\n\nЗаметка')
            for name in ('..', '../outside.txt', 'unknown.pdf'):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    collect_text(root, [name], '')

    def test_export_modes_do_not_overwrite_and_keep_pair_names(self):
        from Text.files import ResultSettings, export_text

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(export_text('Текст', ResultSettings(root)), ())
            self.assertEqual(list(root.iterdir()), [])
            now = datetime(2026, 11, 8, 16, 20)
            first = export_text('Первый', ResultSettings(root, 'Итог', True, True, False), now)
            second = export_text('Второй', ResultSettings(root, 'Итог', True, True, True), now)
            self.assertEqual(first[0].name, '2026-11-08 16-20 Итог.txt')
            self.assertEqual([p.name for p in second], ['2026-11-08 16-20 Итог (2).txt', '2026-11-08 16-20 Итог (2).md'])
            self.assertEqual(first[0].read_text(encoding='utf-8'), 'Первый')
            self.assertEqual(second[1].read_text(encoding='utf-8'), 'Второй')

    def test_existing_server_is_not_stopped_and_missing_model_is_rejected(self):
        from Text.client import Request, generate
        from Text.config import Server

        server = Server('Проверка', 'http://127.0.0.1:9931/v1', {'Gemma': 'gemma'})
        with patch('Text.client.fetch_json', new_callable=AsyncMock) as fetch:
            fetch.side_effect = [
                {'data': [{'id': 'gemma'}]},
                {'choices': [{'message': {'content': 'Ответ'}, 'finish_reason': 'length'}]},
                {'data': [{'id': 'gemma'}]},
            ]
            with patch('Text.client.stop_process', new_callable=AsyncMock) as stop:
                result = asyncio.run(generate(Request(server, 'gemma', 0.2, 32, True), 'Промпт'))
                self.assertEqual((result.text, result.finish_reason), ('Ответ', 'length'))
                self.assertIsNone(result.prompt_tokens)
                self.assertIsNone(result.completion_tokens)
                self.assertEqual(fetch.call_args.kwargs['payload']['messages'], [{'role': 'user', 'content': 'Промпт'}])
                stop.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, 'missing'):
                asyncio.run(generate(Request(server, 'missing', 0.2, 32, False), 'Промпт'))

    def test_generate_keeps_optional_token_counts(self):
        from Text.client import Request, generate
        from Text.config import Server

        server = Server('Проверка', 'http://127.0.0.1:9931/v1', {'Gemma': 'gemma'})
        for usage, expected in (
            ({'prompt_tokens': 5812, 'completion_tokens': 1240}, (5812, 1240)),
            ({'prompt_tokens': 0}, (0, None)),
            ({'prompt_tokens': True, 'completion_tokens': -1}, (None, None)),
            ([], (None, None)),
        ):
            with self.subTest(usage=usage), patch('Text.client.fetch_json', new_callable=AsyncMock) as fetch:
                fetch.side_effect = [
                    {'data': [{'id': 'gemma'}]},
                    {'choices': [{'message': {'content': 'Ответ'}, 'finish_reason': 'stop'}], 'usage': usage},
                ]
                result = asyncio.run(generate(Request(server, 'gemma', 0.2, 32), 'Промпт'))
                counts = (getattr(result, 'prompt_tokens', None), getattr(result, 'completion_tokens', None))
                self.assertEqual(counts, expected)

    def test_chat_reports_explicit_context_errors(self):
        import aiohttp
        from Text.client import fetch_json
        from Text.config import Server

        server = Server('Проверка', 'http://127.0.0.1:9931/v1', {'Gemma': 'gemma'})
        session = MagicMock()
        response = Mock(spec=aiohttp.ClientResponse)
        session.request.return_value.__aenter__.return_value = response
        response.status = 400
        response.raise_for_status.side_effect = aiohttp.ClientResponseError(Mock(), (), status=400)

        for error in ({'type': 'exceed_context_size_error'}, {'code': 'context_length_exceeded'}):
            with self.subTest(error=error), self.assertRaisesRegex(RuntimeError, 'Запрос превышает доступный контекст'):
                response.json.return_value = {'error': error}
                asyncio.run(fetch_json(session, server, '/chat/completions', 1, payload={}))

        response.json.return_value = {'error': {'type': 'invalid_request_error'}}
        with self.assertRaises(aiohttp.ClientResponseError):
            asyncio.run(fetch_json(session, server, '/chat/completions', 1, payload={}))

        response.json.side_effect = ValueError('Invalid JSON')
        with self.assertRaises(aiohttp.ClientResponseError):
            asyncio.run(fetch_json(session, server, '/chat/completions', 1, payload={}))

    def test_missing_key_stops_an_owned_server(self):
        from Text.client import Request, generate
        from Text.config import Server

        address = '127.0.0.1:9931'
        key = 'DSN_TEXT_TEST_MISSING_KEY'
        server = Server('Проверка', f'http://{address}/v1', {'Gemma': 'gemma'}, api_key_env=key)
        process = Mock()
        process.poll.return_value = None
        with (
            patch.dict('os.environ') as env,
            patch.dict('Text.client._owned', {address: process}, clear=True) as owned,
            patch('Text.client.stop_process', new_callable=AsyncMock) as stop,
        ):
            env.pop(key, None)
            with self.assertRaisesRegex(RuntimeError, key):
                asyncio.run(generate(Request(server, 'gemma', 0.2, 32, True), 'Промпт'))
            stop.assert_awaited_once_with(process)
            self.assertNotIn(address, owned)

    def test_library_groups_one_model_across_server_profiles(self):
        from Text.config import load_config
        from Text.library import metadata, model_entries

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            path.write_text(json.dumps({
                'models': {'gemma': 'Gemma', 'unused': 'Unused'},
                'servers': {
                    'local': {'url': 'http://127.0.0.1:9931/v1', 'models': {'gemma': 'local-id'}},
                    'other': {'url': 'http://127.0.0.1:1234/v1', 'models': {'gemma': 'other-id'}},
                },
            }), encoding='utf-8')
            entries = model_entries(load_config(path))
            self.assertEqual([(item['id'], item['name'], item['profiles']) for item in entries], [
                ('gemma', 'Gemma', {'local': 'local-id', 'other': 'other-id'}),
            ])
            self.assertEqual(metadata(entries[0])['description'], 'Серверы: local, other')

    def test_catalog_preserves_manual_annotations_when_a_model_returns(self):
        from app.assets import mode
        from app.assets.database.models import Asset
        from app.assets.services import get_asset_detail, register_file_in_place, update_asset_metadata
        from app.database import db
        from sqlalchemy import select
        from Text.config import Config, Server
        from Text.library import TAGS, model_entries, register_assets

        def card_ids():
            with db.create_session() as session:
                return list(session.scalars(select(Asset.id)))

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(db, 'Session'),
            patch.object(db, 'WriteSession'),
            patch.object(db.args, 'database_url', 'sqlite:///:memory:'),
            patch.object(mode, '_args', db.args),
        ):
            db.init_db()
            with db.create_session() as session:
                self.addCleanup(session.get_bind().dispose)
            root = Path(tmp)
            server = Server('Local', 'http://127.0.0.1:9931/v1', {'Gemma': 'gemma'})
            config = Config(root, root, {'local': server}, {'gemma': 'Gemma'}, root / 'catalog')
            entries = model_entries(config)
            register_assets(config, entries)
            first, = card_ids()
            update_asset_metadata(first, tags=[*TAGS, 'manual-one'],
                                  user_metadata={'note': 'first', 'description': 'old'})
            path = config.catalog / entries[0]['filename']
            duplicate = register_file_in_place(str(path), 'Duplicate', sorted(TAGS), content_written=False)
            update_asset_metadata(duplicate.ref.id, tags=[*TAGS, 'manual-two'],
                                  user_metadata={'note': 'second', 'rating': 5})

            register_assets(config, entries)
            self.assertEqual(len(card_ids()), 1)
            register_assets(config, [])
            self.assertEqual(card_ids(), [])
            self.assertFalse(path.exists())
            with patch('app.assets.services.update_asset_metadata', side_effect=RuntimeError('write failed')):
                with self.assertRaisesRegex(RuntimeError, 'write failed'):
                    register_assets(config, entries)
            register_assets(config, entries)
            restored_id, = card_ids()
            restored = get_asset_detail(restored_id)
            self.assertTrue({'manual-one', 'manual-two'}.issubset(restored.tags))
            self.assertEqual(set(restored.ref.user_metadata['_text_catalog_conflicts']['note']), {'first', 'second'})
            self.assertEqual(restored.ref.user_metadata['rating'], 5)
            self.assertEqual(restored.ref.user_metadata['description'], 'Сервер: Local')

            update_asset_metadata(restored_id, tags=sorted(TAGS), user_metadata={})
            register_assets(config, [])
            register_assets(config, entries)
            cleared = get_asset_detail(card_ids()[0])
            self.assertFalse({'manual-one', 'manual-two'}.intersection(cleared.tags))
            self.assertNotIn('note', cleared.ref.user_metadata)

    def test_new_config_uses_comfy_root_for_manifest_and_launch(self):
        from Text.config import load_config
        from Text.library import metadata, model_entries

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            config_dir = base / 'external-user' / 'package-configs'
            config_dir.mkdir(parents=True)
            (base / 'manifest.json').write_text(
                json.dumps({'source': 'owner/model', 'format': 'GGUF', 'revision': 'abc'}), encoding='utf-8')
            path = config_dir / 'text-config.json'
            path.write_text(json.dumps({
                'root': None,
                'models': {'local': {'name': 'Local model', 'manifest': 'manifest.json'}},
                'servers': {'demo': {'url': 'http://127.0.0.1:8080/v1', 'models': {'local': 'api-id'},
                                     'launch': {'executable': 'bin/server', 'cwd': None}}},
                'defaults': {'request': {'temperature': 0.4, 'stop_after': True}},
            }), encoding='utf-8')
            with patch('Text.config.config_locations', return_value=(base, config_dir)):
                config = load_config(path)
            server = config.servers['demo']
            self.assertEqual(server.cwd, base)
            self.assertEqual(server.launch, (str(base / 'bin' / 'server'),))
            self.assertEqual(server.temperature, 0.4)
            self.assertTrue(server.stop_after)
            card = metadata(model_entries(config)[0])
            self.assertEqual(card['description'], 'Сервер: demo · GGUF · owner/model')
            self.assertEqual(card['revision'], 'abc')
