import asyncio
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT


class SchemaTests(unittest.TestCase):
    def test_each_workflow_has_an_app_with_valid_unlinked_inputs(self):
        workflows = list((ROOT / 'workflows').glob('*.json'))
        self.assertEqual(len(workflows), 8)
        graph_ids = set()
        for path in workflows:
            with self.subTest(path=path.name):
                self.assertTrue(path.name.endswith('.app.json'))
                graph = json.loads(path.read_text(encoding='utf-8'))
                graph_id = graph['id']
                self.assertNotIn(graph_id, graph_ids)
                graph_ids.add(graph_id)
                nodes = {node['id']: node for node in graph['nodes']}
                extra = graph['extra']
                self.assertTrue(extra['linearMode'])
                layout = extra['linearData']
                self.assertEqual(len(layout['outputs']), 1)
                primary = layout['outputs'][0]
                self.assertNotEqual(nodes[primary]['type'], 'FileSetup')
                for key, field in layout['inputs']:
                    stored_id, node_id, widget = key.split(':')
                    self.assertEqual((stored_id, widget), (graph_id, field))
                    slot = next(slot for slot in nodes[int(node_id)]['inputs'] if slot['name'] == field)
                    self.assertEqual(slot.get('widget', {}).get('name'), field)
                    self.assertIsNone(slot['link'])
                if groups := extra.get('speech_result_groups'):
                    self.assertEqual(groups[0][0], primary)
                    self.assertTrue(all(node_id in nodes and title for node_id, title in groups))

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
