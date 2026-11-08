import asyncio
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


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
        from Text.files import collect_text

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'table.csv').write_text('имя,сумма\nтест,1', encoding='utf-8-sig')
            (root / 'note.md').write_text('Заметка', encoding='utf-8')
            self.assertEqual(collect_text(root, 'table.csv\nnote.md', 'Транскрипт'),
                             'Транскрипт\n\n## table.csv\n\nимя,сумма\nтест,1\n\n## note.md\n\nЗаметка')
            for name in ('..', '../outside.txt', 'unknown.pdf'):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    collect_text(root, name, '')

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
                self.assertEqual(result, ('Ответ', 'length'))
                self.assertEqual(fetch.call_args.kwargs['payload']['messages'], [{'role': 'user', 'content': 'Промпт'}])
                stop.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, 'missing'):
                asyncio.run(generate(Request(server, 'missing', 0.2, 32, False), 'Промпт'))

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
