import asyncio
import base64
import copy
import hashlib
import json
import os
import plistlib
import threading
import time
import uuid
from pathlib import Path

from device import Device, flatten, is_folder, json_value, normalized_layout, rebuild_layout, revision, signature


def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(json_value(value), ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def inventory_revision(apps):
    return hashlib.sha256(json.dumps({k: v.get('CFBundleVersion') for k, v in sorted(apps.items())}, sort_keys=True).encode()).hexdigest()


def locations(layout):
    result = {}
    def visit(items, label):
        for position, item in enumerate(items, 1):
            if is_folder(item):
                for n, children in enumerate(item.get('iconLists', []), 1):
                    visit(children, f'{label} / {item.get("displayName", "Folder")} / folder page {n}')
            elif isinstance(item, dict):
                result[item.get('displayIdentifier')] = {'location': f'{label}, position {position}', 'name': item.get('displayName') or 'Special icon'}
    visit(layout[0], 'Dock')
    for n, page in enumerate(layout[1:], 1):
        visit(page, f'Page {n}')
    return result


class Engine:
    def __init__(self, data_dir, device=None):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ['plans', 'backups', 'history', 'choices']:
            (self.root / name).mkdir(exist_ok=True)
        self.device = device or Device()
        self.lock = threading.RLock()
        self.icon_cache = {}

    def call(self, name, args=None):
        args = args or {}
        if not isinstance(args, dict):
            raise ValueError('Arguments must be an object.')
        with self.lock:
            return asyncio.run(self.execute(name, args))

    def path(self, group, key, suffix='.json'):
        try:
            if str(uuid.UUID(key)) != key:
                raise ValueError()
        except (ValueError, AttributeError, TypeError):
            raise ValueError('Invalid saved record identifier.')
        return self.root / group / (key + suffix)

    def read_plan(self, key):
        return json.loads(self.path('plans', key).read_text(encoding='utf-8'))

    def public_plan(self, plan):
        return {k: v for k, v in plan.items() if k not in ('baseline_inventory',)}

    async def backup(self, device_id, state=None):
        state = state if state is not None else await self.device.layout(device_id)
        key = str(uuid.uuid4())
        self.path('backups', key, '.plist').write_bytes(plistlib.dumps(state))
        metadata = {'id': key, 'device_id': device_id, 'created': time.time(), 'pages': len(state) - 1, 'items': len(flatten(state)), 'revision': revision(state)}
        atomic_json(self.path('backups', key), metadata)
        return metadata

    def list_records(self, group):
        result = []
        for path in (self.root / group).glob('*.json'):
            try:
                result.append(json.loads(path.read_text(encoding='utf-8')))
            except (OSError, ValueError):
                continue
        return sorted(result, key=lambda x: x.get('created', 0), reverse=True)

    async def plan_layout(self, args, target=None):
        device_id = args['device_id']
        before = await self.device.layout(device_id)
        if args.get('base_revision') != revision(before):
            raise ValueError('The phone layout changed. Refresh it and make a new plan.')
        target = target if target is not None else rebuild_layout(args['layout'], before,
            protect_page_one=args.get('protect_page_one', True), protect_dock=args.get('protect_dock', True))
        key = str(uuid.uuid4())
        self.path('plans', key, '.before.plist').write_bytes(plistlib.dumps(before))
        self.path('plans', key, '.after.plist').write_bytes(plistlib.dumps(target))
        old, new = locations(before), locations(target)
        changes = [{'key': k, 'name': old[k]['name'], 'from': old[k]['location'], 'to': new[k]['location']} for k in old if old[k]['location'] != new[k]['location']]
        plan = {'id': key, 'kind': 'layout', 'device_id': device_id, 'created': time.time(), 'status': 'staged',
                'base_revision': revision(before), 'layout': normalized_layout(target), 'changes': changes,
                'summary': f'{len(changes)} icon positions change; {len(target)-1} pages after applying.',
                'protected': {'page_one': args.get('protect_page_one', True), 'dock': args.get('protect_dock', True)}}
        atomic_json(self.path('plans', key), plan)
        return plan

    async def execute(self, name, a):
        if name == 'layout.wallpaper':
            try:
                data = await asyncio.wait_for(self.device.wallpaper(a['device_id']), 20)
                if data:
                    return {'available': True, 'image': 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')}
                return {'available': False, 'reason': 'This iPhone did not provide Home Screen wallpaper through its device service.'}
            except Exception as e:
                return {'available': False, 'reason': 'Could not read Home Screen wallpaper. Unlock the phone and retry.', 'detail': str(e)}
        if name == 'apps.icons':
            ids = a.get('identifiers')
            if not isinstance(ids, list) or not ids or len(ids) > 32 or any(not isinstance(i, str) or not i or len(i) > 256 for i in ids):
                raise ValueError('Request between one and 32 icon Identifiers.')
            cache = self.icon_cache.setdefault(a['device_id'], {})
            if a.get('refresh'):
                for i in ids:
                    cache.pop(i, None)
            missing = list(dict.fromkeys(i for i in ids if i not in cache))
            unavailable = []
            if missing:
                images, unavailable = await asyncio.wait_for(self.device.icons(a['device_id'], missing), 180)
                cache.update({i: 'data:image/png;base64,' + base64.b64encode(data).decode('ascii') for i, data in images.items()})
            return {'icons': {i: cache[i] for i in ids if i in cache}, 'unavailable': unavailable}
        if name == 'devices.list':
            return {'devices': await asyncio.wait_for(self.device.devices(), 20)}
        if name == 'apps.list':
            apps = await asyncio.wait_for(self.device.apps(a['device_id'], a.get('calculate_sizes', False)), 120)
            result = []
            for key, app in apps.items():
                app_bytes = app.get('StaticDiskUsage')
                data_bytes = app.get('DynamicDiskUsage')
                result.append({'identifier': key, 'name': app.get('CFBundleDisplayName') or app.get('CFBundleName') or key,
                    'bundle_name': app.get('CFBundleName'), 'type': app.get('ApplicationType'), 'version': app.get('CFBundleShortVersionString'),
                    'app_bytes': app_bytes, 'data_bytes': data_bytes,
                    'total_gib': ((app_bytes or 0) + (data_bytes or 0)) / 2**30 if app_bytes is not None or data_bytes is not None else None,
                    'placeholder': app.get('IsPlaceholder', False)})
            return {'apps': sorted(result, key=lambda x: (x['name'].lower(), x['identifier'])), 'revision': inventory_revision(apps)}
        if name == 'layout.read':
            return normalized_layout(await asyncio.wait_for(self.device.layout(a['device_id']), 30))
        if name == 'layout.plan':
            return await self.plan_layout(a)
        if name == 'layout.move':
            before = await self.device.layout(a['device_id'])
            normalized = normalized_layout(before)
            icon_key = a['icon_key']
            moved = None
            def remove(items):
                nonlocal moved
                for i in range(len(items)-1, -1, -1):
                    item = items[i]
                    if item.get('kind') == 'folder':
                        for page in item['pages']:
                            remove(page)
                        item['pages'] = [p for p in item['pages'] if p]
                        if not item['pages']:
                            items.pop(i)
                    elif item.get('key') == icon_key:
                        moved = items.pop(i)
            remove(normalized['dock'])
            for page in normalized['pages']:
                remove(page)
            if moved is None:
                raise ValueError('Icon not found.')
            n = a['page']
            if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= len(normalized['pages']) + 1:
                raise ValueError('Choose an existing page or the next new page.')
            if n == len(normalized['pages']) + 1:
                normalized['pages'].append([])
            pos = a.get('position', len(normalized['pages'][n-1]) + 1)
            if not isinstance(pos, int) or isinstance(pos, bool) or not 1 <= pos <= len(normalized['pages'][n-1]) + 1:
                raise ValueError('Invalid one-based position.')
            normalized['pages'][n-1].insert(pos - 1, moved)
            normalized['pages'] = [p for p in normalized['pages'] if p]
            return await self.plan_layout({**a, 'layout': normalized, 'base_revision': revision(before)})
        if name == 'apps.plan_delete':
            ids = a.get('identifiers')
            if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)) or any(not isinstance(i, str) for i in ids):
                raise ValueError('Provide a nonempty list of unique app Identifiers.')
            if any(i == 'com.apple' or i.startswith('com.apple.') for i in ids):
                raise ValueError('Apple com.apple apps are excluded from deletion.')
            apps = await self.device.apps(a['device_id'])
            missing = [i for i in ids if i not in apps]
            if missing:
                raise ValueError('Some requested apps are no longer installed. Refresh the inventory.')
            state = await self.device.layout(a['device_id'])
            key = str(uuid.uuid4())
            entries = [{'identifier': i, 'name': apps[i].get('CFBundleDisplayName') or apps[i].get('CFBundleName') or i} for i in ids]
            plan = {'id': key, 'kind': 'delete', 'device_id': a['device_id'], 'created': time.time(), 'status': 'staged',
                    'apps': entries, 'inventory_revision': inventory_revision(apps), 'base_revision': revision(state),
                    'summary': f'Uninstall {len(entries)} apps and their local data. Layout backups do not restore app data.'}
            self.path('plans', key, '.before.plist').write_bytes(plistlib.dumps(state))
            atomic_json(self.path('plans', key), plan)
            return plan
        if name == 'changes.preview':
            return self.public_plan(self.read_plan(a['plan_id']))
        if name == 'changes.list':
            return {'plans': [self.public_plan(p) for p in self.list_records('plans')[:50]]}
        if name == 'changes.discard':
            plan = self.read_plan(a['plan_id'])
            if plan['status'] != 'staged':
                raise ValueError('Only a staged plan can be discarded.')
            plan['status'] = 'discarded'
            atomic_json(self.path('plans', plan['id']), plan)
            return {'discarded': True}
        if name == 'changes.apply':
            if a.get('confirmed') is not True:
                raise ValueError('Explicit approval of the preview is required. Set confirmed to true only after user authorization.')
            plan = self.read_plan(a['plan_id'])
            if plan['status'] != 'staged':
                raise ValueError('This plan has already been applied or attempted. Refresh and create a new plan.')
            before = await self.device.layout(plan['device_id'])
            if revision(before) != plan['base_revision']:
                raise ValueError('The phone layout changed since this preview. Refresh and make a new plan.')
            if plan['kind'] == 'delete':
                if any(x['identifier'] == 'com.apple' or x['identifier'].startswith('com.apple.') for x in plan['apps']):
                    raise ValueError('Apple com.apple apps are excluded from deletion, including older saved plans.')
                apps = await self.device.apps(plan['device_id'])
                if inventory_revision(apps) != plan['inventory_revision']:
                    raise ValueError('Installed apps changed since this deletion preview. Create a new plan.')
            backup = await self.backup(plan['device_id'], before)
            operation_id = str(uuid.uuid4())
            operation = {'id': operation_id, 'plan_id': plan['id'], 'device_id': plan['device_id'], 'kind': plan['kind'], 'created': time.time(),
                         'status': 'applying', 'backup_id': backup['id'], 'results': []}
            plan['status'] = 'applying'
            atomic_json(self.path('plans', plan['id']), plan)
            atomic_json(self.path('history', operation_id), operation)
            try:
                if plan['kind'] == 'layout':
                    expected = plistlib.loads(self.path('plans', plan['id'], '.after.plist').read_bytes())
                    after = await asyncio.wait_for(self.device.write_layout(plan['device_id'], expected), 45)
                    # A separate connection confirms the saved state, not just the write response.
                    after = await self.device.layout(plan['device_id'])
                    self.path('history', operation_id, '.after.plist').write_bytes(plistlib.dumps(after))
                    missing = sorted(set(i['displayIdentifier'] for i in flatten(expected)) - set(i['displayIdentifier'] for i in flatten(after)))
                    added = sorted(set(i['displayIdentifier'] for i in flatten(after)) - set(i['displayIdentifier'] for i in flatten(expected)))
                    operation.update(status='verified' if signature(expected) == signature(after) else 'mismatch',
                        layout=normalized_layout(after), missing=missing, additional=added,
                        page_one_unchanged=signature(before[1]) == signature(after[1]), dock_unchanged=signature(before[0]) == signature(after[0]))
                else:
                    for app in plan['apps']:
                        result = {**app, 'status': 'uninstall requested'}
                        try:
                            await asyncio.wait_for(self.device.uninstall(plan['device_id'], app['identifier']), 90)
                        except Exception as e:
                            result.update(status='request failed', error=str(e))
                        operation['results'].append(result)
                        atomic_json(self.path('history', operation_id), operation)
                    after = await self.device.apps(plan['device_id'])
                    for result in operation['results']:
                        result['status'] = 'still present' if result['identifier'] in after else 'verified absent'
                    targets = {v['identifier'] for v in plan['apps']}
                    operation['unlisted_apps_removed'] = sorted(set(apps) - set(after) - targets)
                    operation['status'] = 'verified' if all(v['status'] == 'verified absent' for v in operation['results']) and not operation['unlisted_apps_removed'] else 'partial'
            except Exception as e:
                operation.update(status='failed', error=str(e), note='The device may have changed. Refresh before retrying; the original layout backup is retained.')
            finally:
                plan['status'] = operation['status']
                atomic_json(self.path('plans', plan['id']), plan)
                atomic_json(self.path('history', operation_id), operation)
            return operation
        if name == 'layouts.list_backups':
            return {'backups': [b for b in self.list_records('backups') if not a.get('device_id') or b['device_id'] == a['device_id']]}
        if name == 'layouts.backup':
            return await self.backup(a['device_id'])
        if name == 'layouts.plan_restore':
            metadata = json.loads(self.path('backups', a['backup_id']).read_text())
            if metadata['device_id'] != a['device_id']:
                raise ValueError('This backup belongs to a different phone.')
            saved = plistlib.loads(self.path('backups', a['backup_id'], '.plist').read_bytes())
            before = await self.device.layout(a['device_id'])
            normalized = normalized_layout(saved)
            target = rebuild_layout(normalized, before, a.get('protect_page_one', True), a.get('protect_dock', True))
            return await self.plan_layout({**a, 'base_revision': revision(before)}, target)
        if name == 'history.list':
            return {'operations': self.list_records('history')[:100]}
        if name == 'choices.read':
            key = hashlib.sha256(a['device_id'].encode()).hexdigest()
            path = self.root / 'choices' / (key + '.json')
            return json.loads(path.read_text()) if path.exists() else {'choices': {}, 'history': []}
        if name == 'choices.save':
            value = a.get('choices')
            if not isinstance(value, dict) or any(not isinstance(k, str) or v not in ('keep', 'delete') for k, v in value.items()):
                raise ValueError('Invalid sorting choices.')
            history = a.get('history', [])
            if not isinstance(history, list) or any(not isinstance(h, dict) or not isinstance(h.get('identifier'), str) or h.get('before') not in (None, 'keep', 'delete') for h in history):
                raise ValueError('Invalid sorting history.')
            key = hashlib.sha256(a['device_id'].encode()).hexdigest()
            atomic_json(self.root / 'choices' / (key + '.json'), {'choices': value, 'history': history, 'savedAt': time.time()*1000})
            return {'saved': True}
        raise ValueError('Unknown operation: ' + str(name))

