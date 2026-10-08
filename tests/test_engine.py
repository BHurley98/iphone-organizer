import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import bootstrap
from device import normalized_layout, rebuild_layout, revision
from engine import Engine
from server import rpc


def icon(key, name=None):
    return {'displayIdentifier': key, 'bundleIdentifier': key, 'displayName': name or key, 'iconModDate': 'unchanged'}


class FakeDevice:
    def __init__(self):
        self.state = [[icon('dock')], [icon('first')], [icon('one', 'Duplicate'), icon('two', 'Duplicate'),
            {'listType': 'folder', 'displayName': 'Original', 'iconLists': [[icon('three')]]}], [icon('four')]]
        self.inventory = {key: {'CFBundleDisplayName': key, 'CFBundleVersion': '1', 'StaticDiskUsage': 2**30, 'DynamicDiskUsage': 2**29} for key in ['dock', 'first', 'one', 'two', 'three', 'four']}
        self.writes = 0
        self.deletions = []
        self.fail_delete = None
        self.fail_write = False
        self.mismatch = False

    async def devices(self):
        return [{'id': 'phone', 'available': True, 'name': 'Test phone'}]

    async def layout(self, _):
        return copy.deepcopy(self.state)

    async def apps(self, _, sizes=False):
        return copy.deepcopy(self.inventory)

    async def write_layout(self, _, state):
        self.writes += 1
        if self.fail_write:
            raise RuntimeError('Disconnected during write')
        self.state = copy.deepcopy(state)
        if self.mismatch:
            self.state[2].append(icon('unexpected'))
        return copy.deepcopy(self.state)

    async def uninstall(self, _, key):
        self.deletions.append(key)
        if key == self.fail_delete:
            raise RuntimeError('App removal refused')
        self.inventory.pop(key, None)


class EngineTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parent / '.scratch'
        scratch.mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=scratch)
        assert Path(self.directory.name).resolve().parent == scratch.resolve()
        self.device = FakeDevice()
        self.engine = Engine(self.directory.name, self.device)

    def tearDown(self):
        self.directory.cleanup()

    def plan_move(self):
        return self.engine.call('layout.move', {'device_id': 'phone', 'icon_key': 'one', 'page': 3, 'position': 1})

    def test_move_is_staged_and_exact(self):
        original = copy.deepcopy(self.device.state)
        plan = self.plan_move()
        self.assertEqual(self.device.state, original)
        self.assertEqual(plan['layout']['pages'][2][0]['key'], 'one')
        result = self.engine.call('changes.apply', {'plan_id': plan['id'], 'confirmed': True})
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(self.device.state[:2], original[:2])
        self.assertEqual(self.device.state[3][0]['iconModDate'], 'unchanged')
        self.assertEqual(len(self.engine.call('layouts.list_backups')['backups']), 1)

    def test_explicit_confirmation_required(self):
        plan = self.plan_move()
        for value in [False, None, 'true', 1]:
            with self.assertRaises(ValueError):
                self.engine.call('changes.apply', {'plan_id': plan['id'], 'confirmed': value})
        self.assertEqual(self.device.writes, 0)

    def test_stale_layout_rejected(self):
        plan = self.plan_move()
        self.device.state[3].append(icon('new'))
        with self.assertRaises(ValueError):
            self.engine.call('changes.apply', {'plan_id': plan['id'], 'confirmed': True})
        self.assertEqual(self.device.writes, 0)

    def test_protected_page_and_dock(self):
        for key in ['first', 'dock']:
            with self.assertRaises(ValueError):
                self.engine.call('layout.move', {'device_id': 'phone', 'icon_key': key, 'page': 3})

    def test_missing_and_duplicate_icons_rejected(self):
        layout = normalized_layout(self.device.state)
        layout['pages'][1].pop(0)
        with self.assertRaises(ValueError):
            rebuild_layout(layout, self.device.state)
        layout = normalized_layout(self.device.state)
        layout['pages'][2].append(copy.deepcopy(layout['pages'][1][0]))
        with self.assertRaises(ValueError):
            rebuild_layout(layout, self.device.state)

    def test_new_folders_preserve_duplicate_names(self):
        layout = normalized_layout(self.device.state)
        children = layout['pages'][1][:2]
        layout['pages'][1] = [layout['pages'][1][2]]
        layout['pages'][2].append({'kind': 'folder', 'name': 'New folder', 'pages': [children]})
        plan = self.engine.call('layout.plan', {'device_id': 'phone', 'base_revision': layout['revision'], 'layout': layout})
        result = self.engine.call('changes.apply', {'plan_id': plan['id'], 'confirmed': True})
        self.assertEqual(result['status'], 'verified')
        self.assertEqual([x['displayIdentifier'] for x in self.device.state[3][-1]['iconLists'][0]], ['one', 'two'])

    def test_special_icon_metadata_preserved(self):
        special = icon('special'); special.update(iconType='app', iconLists=[], bundleIdentifier='bible')
        self.device.state[3].append(special)
        layout = normalized_layout(self.device.state)
        rebuilt = rebuild_layout(layout, self.device.state)
        self.assertEqual(rebuilt[3][-1], special)

    def test_app_deletion_exact_and_verified(self):
        plan = self.engine.call('apps.plan_delete', {'device_id': 'phone', 'identifiers': ['one']})
        self.assertIn('one', self.device.inventory)
        result = self.engine.call('changes.apply', {'plan_id': plan['id'], 'confirmed': True})
        self.assertEqual(self.device.deletions, ['one'])
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['unlisted_apps_removed'], [])
        self.assertIn('two', self.device.inventory)

    def test_partial_deletion_honest(self):
        self.device.fail_delete = 'two'
        plan = self.engine.call('apps.plan_delete', {'device_id': 'phone', 'identifiers': ['one', 'two']})
        result = self.engine.call('changes.apply', {'plan_id': plan['id'], 'confirmed': True})
        self.assertEqual(result['status'], 'partial')
        self.assertEqual([r['status'] for r in result['results']], ['verified absent', 'still present'])

    def test_inventory_change_blocks_delete(self):
        plan = self.engine.call('apps.plan_delete', {'device_id': 'phone', 'identifiers': ['one']})
        self.device.inventory['one']['CFBundleVersion'] = '2'
        with self.assertRaises(ValueError):
            self.engine.call('changes.apply', {'plan_id': plan['id'], 'confirmed': True})
        self.assertEqual(self.device.deletions, [])

    def test_write_failure_journal_and_backup(self):
        plan = self.plan_move(); self.device.fail_write = True
        result = self.engine.call('changes.apply', {'plan_id': plan['id'], 'confirmed': True})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(self.engine.call('layouts.list_backups')['backups']), 1)
        with self.assertRaises(ValueError):
            self.engine.call('changes.apply', {'plan_id': plan['id'], 'confirmed': True})

    def test_mismatch_reported(self):
        plan = self.plan_move(); self.device.mismatch = True
        result = self.engine.call('changes.apply', {'plan_id': plan['id'], 'confirmed': True})
        self.assertEqual(result['status'], 'mismatch')
        self.assertEqual(result['additional'], ['unexpected'])

    def test_restore_same_device_and_icons(self):
        backup = self.engine.call('layouts.backup', {'device_id': 'phone'})
        plan = self.plan_move()
        self.engine.call('changes.apply', {'plan_id': plan['id'], 'confirmed': True})
        restored = self.engine.call('layouts.plan_restore', {'device_id': 'phone', 'backup_id': backup['id']})
        self.assertEqual(restored['kind'], 'layout')
        with self.assertRaises(ValueError):
            self.engine.call('layouts.plan_restore', {'device_id': 'wrong-phone', 'backup_id': backup['id']})

    def test_sorting_persistence_and_invalid_values(self):
        self.engine.call('choices.save', {'device_id': 'phone', 'choices': {'one': 'keep', 'two': 'delete'}, 'history': [{'identifier': 'two', 'before': None}]})
        second = Engine(self.directory.name, self.device)
        self.assertEqual(second.call('choices.read', {'device_id': 'phone'})['choices']['two'], 'delete')
        with self.assertRaises(ValueError):
            second.call('choices.save', {'device_id': 'phone', 'choices': {'one': 'uninstall'}})

    def test_apple_deletion_excluded_in_new_and_old_plans(self):
        self.device.inventory['com.apple.Test'] = {'CFBundleVersion': '1'}
        with self.assertRaisesRegex(ValueError, 'Apple'):
            self.engine.call('apps.plan_delete', {'device_id': 'phone', 'identifiers': ['com.apple.Test']})
        plan = self.engine.call('apps.plan_delete', {'device_id': 'phone', 'identifiers': ['one']})
        path = self.engine.path('plans', plan['id'])
        saved = json.loads(path.read_text())
        saved['apps'][0]['identifier'] = 'com.apple.Test'
        path.write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError, 'Apple'):
            self.engine.call('changes.apply', {'plan_id': plan['id'], 'confirmed': True})
        self.assertEqual(self.device.deletions, [])

    def test_icons_cached_by_device_and_refreshable(self):
        calls = []
        async def icons(device_id, ids):
            calls.append((device_id, ids))
            return {i: b'PNG test bytes' for i in ids if i != 'unknown'}, ['unknown'] if 'unknown' in ids else []
        self.device.icons = icons
        result = self.engine.call('apps.icons', {'device_id': 'phone', 'identifiers': ['one', 'unknown']})
        self.assertTrue(result['icons']['one'].startswith('data:image/png;base64,'))
        self.assertEqual(result['unavailable'], ['unknown'])
        self.engine.call('apps.icons', {'device_id': 'phone', 'identifiers': ['one']})
        self.assertEqual(len(calls), 1)
        self.engine.call('apps.icons', {'device_id': 'phone', 'identifiers': ['one'], 'refresh': True})
        self.engine.call('apps.icons', {'device_id': 'other', 'identifiers': ['one']})
        self.assertEqual(len(calls), 3)

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.call('changes.preview', {'plan_id': '../../outside'})

    def test_mcp_lifecycle_and_tool_calls(self):
        init = rpc(self.engine, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25'}})
        self.assertEqual(init['result']['protocolVersion'], '2025-11-25')
        self.assertIsNone(rpc(self.engine, {'jsonrpc': '2.0', 'method': 'notifications/initialized'}))
        listed = rpc(self.engine, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
        self.assertGreaterEqual(len(listed['result']['tools']), 10)
        called = rpc(self.engine, {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'layout.read', 'arguments': {'device_id': 'phone'}}})
        self.assertFalse(called['result']['isError'])
        bad = rpc(self.engine, {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'changes.apply', 'arguments': {'plan_id': 'no', 'confirmed': 'true'}}})
        self.assertTrue(bad['result']['isError'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
