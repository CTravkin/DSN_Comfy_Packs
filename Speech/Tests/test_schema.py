import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT


class SchemaTests(unittest.TestCase):
    def test_seven_nodes_expose_readable_names_and_separate_russian_labels(self):
        self.assertTrue((PACKAGE / '__init__.py').is_file(), 'Comfy extension is not implemented')
        spec = importlib.util.spec_from_file_location('speech_schema_tested', PACKAGE / '__init__.py', submodule_search_locations=[str(PACKAGE)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = package
        spec.loader.exec_module(package)
        extension = asyncio.run(package.comfy_entrypoint())
        classes = asyncio.run(extension.get_node_list())
        names = {node.define_schema().display_name for node in classes}
        self.assertEqual(names, {'File Setup', 'Whisper', 'Speaker Diarization', 'Voiceprint', 'Voiceprint Compare', 'Voiceprint Identity', 'Speaker Transcript'})
        for node in classes:
            schema = node.define_schema()
            self.assertNotIn('DSN', schema.node_id)
            self.assertNotIn('Local', schema.display_name)
            for field in schema.inputs:
                self.assertTrue(field.display_name, (schema.node_id, field.id))
                self.assertTrue(any('А' <= c <= 'я' for c in field.display_name), field.display_name)


if __name__ == '__main__':
    unittest.main()
