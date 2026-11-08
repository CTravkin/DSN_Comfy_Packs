from __future__ import annotations

import importlib
import json
import sys
import tempfile
import textwrap
import types
import unittest
from unittest.mock import patch
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT


class SpeechTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (PACKAGE / 'files.py').is_file():
            return
        package = types.ModuleType('speech_under_test')
        package.__path__ = [str(PACKAGE)]
        sys.modules[package.__name__] = package
        cls.files = importlib.import_module('speech_under_test.files')
        cls.catalog = importlib.import_module('speech_under_test.catalog')
        cls.runtime = importlib.import_module('speech_under_test.runtime')
        cls.library = importlib.import_module('speech_under_test.library')

    def setUp(self):
        self.assertTrue((PACKAGE / 'files.py').is_file(), 'Speech file handling is not implemented')
        self.temp = tempfile.TemporaryDirectory(prefix='speech-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.media = self.root / 'Запись & проба.wav'
        self.media.write_bytes(b'media-fixture')
        self.now = datetime(2026, 11, 5, 10, 50)

    def setup_files(self, **kwargs):
        return self.files.setup_files(str(self.media), str(self.root / 'Output'), now=self.now, **kwargs)

    def test_config_separates_comfy_base_user_and_model_directories(self):
        config_module = importlib.import_module('speech_under_test.config')
        user = self.root / 'other-user'
        models = self.root / 'external-models' / 'speech'
        path = user / 'package-configs' / 'speech-config.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'root': None, 'paths': {'model_roots': None},
                                    'defaults': {'whisper': {'export': {'txt': True}}}}), encoding='utf-8')
        with patch.object(config_module, 'config_locations', return_value=(self.root, user, models)):
            settings = config_module.load_config(path)
        self.assertEqual(settings.root, self.root.resolve())
        self.assertEqual(settings.model_roots, (models.resolve(),))
        self.assertEqual(settings.paths['model_catalog'], path.parent.resolve() / 'speech-model-catalog')
        self.assertEqual(settings.paths['voiceprints'], self.root.resolve() / 'Data' / 'Speakers')
        self.assertEqual(settings.defaults['whisper']['export'], {'txt': True, 'json': False, 'srt': False, 'vtt': False})

    def test_file_setup_resolves_paths_from_working_root(self):
        for source, output in ((self.media.name, 'Результаты'), (str(self.media), str(self.root / 'Результаты'))):
            context = self.files.setup_files(source, output, 'Проверка', False, root=self.root)
            self.assertEqual(context.source, self.media.resolve())
            self.assertEqual(context.directory, self.root.resolve() / 'Результаты' / 'Проверка')
        with self.assertRaisesRegex(ValueError, 'за пределами'):
            self.files.read_path('../другая запись.wav', self.root)

    def test_timestamp_and_name_are_shared_by_folder_and_export(self):
        context = self.setup_files(name='Обсуждение сметы', include_datetime=True)
        self.assertEqual(context.name, '2026-11-05 10-50 Обсуждение сметы')
        self.assertEqual(context.directory.name, context.name)
        source = context.data / 'recognition.txt'
        source.write_text('Результат', encoding='utf-8')
        output = self.files.publish(context, source, '.txt')
        self.assertEqual(output.name, '2026-11-05 10-50 Обсуждение сметы.txt')
        self.assertEqual(output.read_text(encoding='utf-8'), 'Результат')

    def test_date_can_be_disabled_and_empty_name_falls_back_to_source(self):
        self.assertEqual(self.setup_files(name='Проверка', include_datetime=False).name, 'Проверка')
        self.assertEqual(self.setup_files(name='', include_datetime=False).name, 'Запись & проба')

    def test_same_minute_runs_share_folder_and_keep_separate_stage_files(self):
        first = self.setup_files(name='', include_datetime=True)
        marker = first.stage('recognition') / 'keep.txt'
        marker.write_text('existing', encoding='utf-8')
        second = self.setup_files(name='', include_datetime=True)
        self.assertEqual(first.directory, second.directory)
        self.assertNotEqual(marker.parent, second.stage('recognition'))
        self.assertEqual(marker.read_text(), 'existing')

    def test_names_cannot_escape_output_directory(self):
        for bad in ['../escape', r'C:\elsewhere', 'NUL', 'CON.txt', 'a/b']:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.setup_files(name=bad, include_datetime=False)

    def test_changed_recording_is_rejected_before_reuse(self):
        context = self.setup_files()
        self.media.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'измен'):
            context.validate_source()

    def test_exports_add_numbers_before_the_complete_extension(self):
        context = self.setup_files(name='Проверка', include_datetime=False)
        source = context.data / 'result.json'
        names = ['Проверка.voiceprint.json', 'Проверка (2).voiceprint.json', 'Проверка (3).voiceprint.json']
        for name in names:
            source.write_text(name, encoding='utf-8')
            exported = self.files.publish(context, source, '.voiceprint.json')
            self.assertEqual(exported, context.directory / name)
        for name in names:
            self.assertEqual((context.directory / name).read_text(encoding='utf-8'), name)

    def test_missing_stage_source_cannot_delete_an_existing_export(self):
        context = self.setup_files()
        existing = context.directory / (context.name + '.txt')
        existing.write_text('keep', encoding='utf-8')
        with self.assertRaises((FileExistsError, FileNotFoundError)):
            self.files.publish(context, context.data / 'missing.txt', '.txt')
        self.assertTrue(existing.is_file(), 'Existing export was deleted')
        self.assertEqual(existing.read_text(encoding='utf-8'), 'keep')

    def test_catalog_uses_hugging_face_identity_and_human_name(self):
        folder = self.root / 'Models' / 'Whisper' / 'large-v3-turbo-ct2'
        folder.mkdir(parents=True)
        (folder / 'model.bin').write_bytes(b'weights')
        (folder / 'manifest.json').write_text(json.dumps({
            'source': 'dropbox-dash/faster-whisper-large-v3-turbo',
            'revision': 'fixed-revision', 'display_name': 'Whisper Large V3 Turbo CT2',
            'format': 'CTranslate2 FP16', 'weight_file': {'path': 'model.bin'}
        }), encoding='utf-8')
        models = self.catalog.load_models(self.root / 'Models')
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0].id, 'dropbox-dash/faster-whisper-large-v3-turbo')
        self.assertEqual(models[0].display_name, 'Whisper Large V3 Turbo CT2')
        self.assertEqual(models[0].family, 'whisper')
        self.assertEqual(models[0].path, folder)
        (folder / 'model.bin').unlink()
        self.assertEqual(self.catalog.load_models(self.root / 'Models'), [])

    def test_catalog_descriptors_represent_complete_bundles(self):
        model = self.catalog.Model('pyannote/speaker-diarization-community-1', 'Pyannote Speaker Diarization Community 1', 'diarization', self.root, 'rev', {})
        records = self.catalog.write_descriptors([model], self.root / 'catalog')
        self.assertEqual(len(records), 1)
        content = json.loads(records[0].read_text(encoding='utf-8'))
        self.assertEqual(content['source'], model.id)
        self.assertEqual(Path(content['local_path']).resolve(), self.root.resolve())

    def test_descriptor_identity_changes_when_visible_metadata_changes(self):
        first = self.catalog.Model('owner/model', 'First', 'whisper', self.root, 'rev', {})
        renamed = self.catalog.Model('owner/model', 'Renamed', 'whisper', self.root, 'rev', {})
        self.assertNotEqual(first.descriptor_name, renamed.descriptor_name)

    def test_duplicate_card_annotations_preserve_tags_and_conflicting_notes(self):
        self.assertTrue(hasattr(self.library, 'merge_annotations'), 'Card annotation preservation is missing')
        result = self.library.merge_annotations([
            (['models', 'Favorite'], {'source': 'owner/model', 'notes': 'First note', 'color': 'blue'}),
            (['model_type:speech', 'Reviewed'], {'notes': 'Second note', 'custom': {'value': 42}}),
        ])
        self.assertEqual(result['tags'], ['Favorite', 'Reviewed'])
        self.assertEqual(result['metadata']['color'], 'blue')
        self.assertEqual(result['metadata']['custom'], {'value': 42})
        self.assertEqual(result['metadata']['_speech_catalog_conflicts']['notes'], ['First note', 'Second note'])
        self.assertNotIn('source', result['metadata'])

    def test_new_scanner_card_restores_archive_but_managed_clear_is_honored(self):
        archive = {'tags': ['Favorite'], 'metadata': {'notes': 'Keep this note'}}
        restored = self.library.merge_annotations([([], {})], previous=archive, managed=False)
        self.assertEqual(restored, archive)
        cleared = self.library.merge_annotations([([], {})], previous=archive, managed=True)
        self.assertEqual(cleared, {'tags': [], 'metadata': {}})

    def test_invalid_model_identity_cannot_escape_catalog(self):
        for bad in [r'..\..\outside', '../outside', 'owner/../../outside', '/absolute', 'owner/repo:bad']:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                model = self.catalog.Model(bad, 'Bad', 'whisper', self.root, 'rev', {})
                self.catalog.write_descriptors([model], self.root / 'nested' / 'nested' / 'catalog')
        self.assertFalse((self.root / 'outside.json').exists())

    def test_voiceprint_cache_is_isolated_by_model_and_revision(self):
        first = self.catalog.Model('owner/first', 'First', 'voiceprint', self.root / 'first', 'rev1', {})
        other = self.catalog.Model('owner/second', 'Second', 'voiceprint', self.root / 'second', 'rev1', {})
        revision = self.catalog.Model('owner/first', 'First 2', 'voiceprint', self.root / 'first', 'rev2', {})
        paths = [self.runtime.voiceprint_cache(model, self.root) for model in [first, other, revision]]
        self.assertEqual(len(set(paths)), 3)
        self.assertTrue(all(path.is_relative_to(self.root / 'Cache') for path in paths))

    def test_blank_reference_directory_uses_the_selected_ai_root(self):
        model = self.catalog.Model('owner/voices', 'Voices', 'voiceprint', self.root, 'rev', {})
        for configured in ['', '   ', str(self.root / 'Custom References')]:
            with self.subTest(configured=configured):
                context = self.setup_files()
                payload = {'source': str(self.media), 'source_sha256': context.source_sha256}
                diarization = self.root / 'diarization.json'
                diarization.write_text(json.dumps(payload), encoding='utf-8')
                def canonical_cli(tool, arguments, **kwargs):
                    destination = Path(arguments[arguments.index('--output-json') + 1])
                    destination.write_text(json.dumps(payload), encoding='utf-8')
                with patch.object(self.runtime, 'invoke', side_effect=canonical_cli) as invoke:
                    self.runtime.identify(context, model, str(diarization), configured, ai_root=self.root)
                arguments = invoke.call_args.args[1]
                actual = Path(arguments[arguments.index('--reference-root') + 1])
                expected = Path(configured) if configured.strip() else self.root / 'Data' / 'Speakers'
                self.assertEqual(actual, expected.resolve())

    def test_reused_diarization_returns_the_new_numbered_rttm_path(self):
        context = self.setup_files()
        model = self.catalog.Model('owner/pipeline', 'Pipeline', 'diarization', self.root, 'rev', {})
        existing = self.root / 'existing.json'
        existing.write_text(json.dumps({'source': str(self.media), 'source_sha256': context.source_sha256,
            'model': str(self.root), 'parameters': {}, 'speakers': ['SPEAKER_00'],
            'segments': [{'start': 1.25, 'end': 2.75, 'speaker': 'SPEAKER_00'}],
            'outputs': {'rttm': str(self.root / 'missing.rttm')}}), encoding='utf-8')
        old_rttm = context.directory / (context.name + '.rttm')
        old_rttm.write_text('previous', encoding='utf-8')
        _, count, exported = self.runtime.diarize(context, model, existing_json=str(existing), rttm=True, ai_root=self.root)
        self.assertEqual(count, 1)
        self.assertEqual(exported, context.directory / (context.name + ' (2).rttm'))
        self.assertEqual(old_rttm.read_text(encoding='utf-8'), 'previous')
        self.assertIn('1.250 1.500 <NA> <NA> SPEAKER_00', exported.read_text(encoding='utf-8'))

    def test_runner_preserves_literal_arguments_and_enforces_offline_mode(self):
        cli = self.root / 'fake CLI.py'
        cli.write_text(textwrap.dedent('''\
            import json, os, sys
            print(json.dumps({'arguments': sys.argv[1:], 'offline': os.environ.get('HF_HUB_OFFLINE'), 'transformers': os.environ.get('TRANSFORMERS_OFFLINE')}, ensure_ascii=False))
        '''), encoding='utf-8')
        result = self.runtime.run_cli(Path(sys.executable), cli, ['--input', str(self.media), '--prompt', 'имя & контекст'], ai_root=self.root)
        self.assertEqual(result['arguments'], ['--input', str(self.media), '--prompt', 'имя & контекст'])
        self.assertEqual(result['offline'], '1')
        self.assertEqual(result['transformers'], '1')

    def test_runner_reports_failure_and_timeout_without_speech_logs(self):
        cli = self.root / 'failure.py'
        cli.write_text("import sys; print('private transcription', file=sys.stderr); sys.exit(7)", encoding='utf-8')
        with self.assertRaises(RuntimeError) as failure:
            self.runtime.run_cli(Path(sys.executable), cli, [], ai_root=self.root)
        self.assertIn('7', str(failure.exception))
        self.assertNotIn('private transcription', str(failure.exception))
        cli.write_text('import time; time.sleep(30)', encoding='utf-8')
        with self.assertRaises(TimeoutError):
            self.runtime.run_cli(Path(sys.executable), cli, [], ai_root=self.root, timeout=0.1)

    def test_runner_honors_cancellation(self):
        cli = self.root / 'cancel.py'
        cli.write_text('import time; time.sleep(30)', encoding='utf-8')
        def cancel():
            raise InterruptedError('cancel')
        with self.assertRaises(InterruptedError):
            self.runtime.run_cli(Path(sys.executable), cli, [], ai_root=self.root, cancel_check=cancel)

    def test_unsupported_translation_is_rejected_from_manifest_capability(self):
        model = self.catalog.Model('owner/fast-model', 'Fast model', 'whisper', self.root, 'rev', {'tasks': ['transcribe']})
        with self.assertRaisesRegex(ValueError, 'не поддерживает'):
            self.runtime.whisper(self.setup_files(), model, task='translate', ai_root=self.root)

    def test_disabling_text_export_preserves_formatted_transcript_output(self):
        context = self.setup_files()
        payload = {'source': str(self.media), 'source_sha256': context.source_sha256,
                   'turns': [{'speaker': 'SPEAKER_00', 'text': 'Проверка', 'identity_status': 'candidate'}]}
        recognition = self.root / 'recognition.json'
        diarization = self.root / 'diarization.json'
        for path in (recognition, diarization):
            path.write_text(json.dumps(payload), encoding='utf-8')
        formatted = '[00:01] SPEAKER_00 (кандидат: Тест): Проверка\n'
        def canonical_cli(tool, arguments, **kwargs):
            destination = Path(arguments[arguments.index('--output-json') + 1])
            destination.write_text(json.dumps(payload), encoding='utf-8')
            if '--output-txt' in arguments:
                Path(arguments[arguments.index('--output-txt') + 1]).write_text(formatted, encoding='utf-8')
            return {'json': str(destination)}
        with patch.object(self.runtime, 'invoke', side_effect=canonical_cli):
            _, text, outputs = self.runtime.transcript(context, str(recognition), str(diarization), save_json=False, save_text=False, ai_root=self.root)
        self.assertEqual(text, formatted)
        self.assertEqual(outputs, {})
        self.assertFalse((context.directory / (context.name + '.txt')).exists())


if __name__ == '__main__':
    unittest.main()
