import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from comfy_execution.jobs import get_outputs_summary
from Speech import results


class ResultTests(unittest.TestCase):
    def test_saved_text_is_primary_and_metadata_is_not_an_artifact(self):
        with TemporaryDirectory() as scratch:
            output = Path(scratch).resolve()
            run = output / 'run'
            run.mkdir()
            txt, srt = run / 'result.txt', run / 'result.srt'
            txt.write_text('Text', encoding='utf-8')
            srt.write_text('Subtitles', encoding='utf-8')
            node = SimpleNamespace(hidden=SimpleNamespace(extra_pnginfo={
                'workflow': {'extra': {'speech_view_key': 'current-view'}}}))
            with patch.object(results.folder_paths, 'get_output_directory', return_value=str(output)):
                payload = results.presentation(node, SimpleNamespace(directory=run), 'Готово', [srt, txt], text='Text')
            count, primary = get_outputs_summary({'2': payload})
            self.assertEqual(count, 2)
            self.assertEqual(primary['filename'], 'result.txt')
            self.assertEqual(primary['type'], 'output')
            self.assertEqual(json.loads(payload['speech_result'][0])['view_key'], 'current-view')

    def test_folder_action_requires_local_origin_and_matching_host(self):
        def allowed(remote, origin):
            return results.is_local_request(SimpleNamespace(
                remote=remote, host='127.0.0.1:8188', headers={'Origin': origin}))
        self.assertTrue(allowed('127.0.0.1', 'http://127.0.0.1:8188'))
        self.assertFalse(allowed('192.0.2.2', 'http://127.0.0.1:8188'))
        self.assertFalse(allowed('127.0.0.1', 'http://127.0.0.1:8199'))
        self.assertFalse(allowed('127.0.0.1', 'https://example.test'))
        self.assertFalse(allowed('127.0.0.1', ''))
