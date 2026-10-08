"""Only the tested USB services. Every connection is explicitly device-bound."""
import asyncio
import contextlib
import copy
import datetime
import hashlib
import json
import plistlib


def json_value(value):
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {'binary_bytes': len(value)}
    return value


def signature(value):
    if isinstance(value, list):
        return [signature(v) for v in value]
    if not isinstance(value, dict):
        return value
    if value.get('listType') == 'folder' or (value.get('iconLists') and not value.get('bundleIdentifier')):
        return {'folder': value.get('displayName'), 'pages': signature(value.get('iconLists', []))}
    return {k: signature(value[k]) for k in ('displayIdentifier', 'bundleIdentifier', 'displayName', 'iconType', 'webClipURL', 'iconLists') if k in value}


def revision(state):
    return hashlib.sha256(json.dumps(signature(state), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def is_folder(icon):
    return isinstance(icon, dict) and (icon.get('listType') == 'folder' or (bool(icon.get('iconLists')) and not icon.get('bundleIdentifier')))


def flatten(state):
    result = []
    for item in state:
        if isinstance(item, list):
            result.extend(flatten(item))
        elif is_folder(item):
            result.extend(flatten(item.get('iconLists', [])))
        elif isinstance(item, dict):
            result.append(item)
    return result


def normalized_layout(state):
    def convert(icon):
        if not isinstance(icon, dict):
            return {'kind': 'spacer', 'key': str(icon), 'name': 'Empty space'}
        if is_folder(icon):
            return {'kind': 'folder', 'name': icon.get('displayName', 'Folder'), 'pages': [[convert(v) for v in p] for p in icon.get('iconLists', [])]}
        return {'kind': 'icon', 'key': icon.get('displayIdentifier'), 'identifier': icon.get('bundleIdentifier'),
                'name': icon.get('displayName') or ('Bible special icon' if icon.get('bundleIdentifier') == 'tv.lifechurch.bible' else 'Special icon'),
                'special': bool(icon.get('iconLists') is not None and icon.get('iconType'))}
    return {'dock': [convert(x) for x in state[0]], 'pages': [[convert(x) for x in p] for p in state[1:]], 'revision': revision(state)}


def rebuild_layout(layout, before, protect_page_one=True, protect_dock=True):
    """Use original icon records; never manufacture app identifiers or strip widget metadata."""
    if not isinstance(layout, dict) or not isinstance(layout.get('pages'), list) or not isinstance(layout.get('dock'), list):
        raise ValueError('A layout must contain dock and pages arrays.')
    originals = {i.get('displayIdentifier'): i for i in flatten(before)}
    if len(originals) != len(flatten(before)):
        raise ValueError('Duplicate icon keys on this phone need separate handling.')
    used = set()

    def convert(item, inside_folder=False):
        if not isinstance(item, dict):
            raise ValueError('Invalid layout item.')
        if item.get('kind') == 'folder':
            if inside_folder:
                raise ValueError('Nested folders are not supported.')
            name = item.get('name', '').strip()
            pages = item.get('pages')
            if not name or len(name) > 60 or not isinstance(pages, list) or not pages:
                raise ValueError('A folder needs a name and at least one nonempty page.')
            if any(not isinstance(p, list) or not p or len(p) > 9 for p in pages):
                raise ValueError('Each folder page holds up to nine icons.')
            return {'displayName': name, 'listType': 'folder', 'iconLists': [[convert(x, True) for x in p] for p in pages]}
        if item.get('kind') == 'spacer':
            raise ValueError('Layouts with spacer records require additional device-specific testing.')
        key = item.get('key')
        if key not in originals or key in used:
            raise ValueError('Every original icon must appear exactly once; an icon is missing, unknown, or repeated.')
        if inside_folder and originals[key].get('iconType') and 'iconLists' in originals[key]:
            raise ValueError('Keep special icons outside folders.')
        used.add(key)
        return copy.deepcopy(originals[key])

    if not layout['pages'] or len(layout['pages']) > 15:
        raise ValueError('Use between one and fifteen Home Screen pages.')
    if len(layout['dock']) > 4:
        raise ValueError('This iPhone dock holds up to four icons.')
    if any(not isinstance(p, list) or not p or len(p) > 24 for p in layout['pages']):
        raise ValueError('Pages must be nonempty and hold no more than 24 layout entries. Special icons can use more grid space.')
    result = [[convert(x) for x in layout['dock']]] + [[convert(x) for x in p] for p in layout['pages']]
    if used != set(originals):
        raise ValueError('This plan would omit existing Home Screen items. Move them into a page or folder instead.')
    if protect_dock and signature(result[0]) != signature(before[0]):
        raise ValueError('The dock is protected.')
    if protect_page_one and signature(result[1]) != signature(before[1]):
        raise ValueError('Page one is protected.')
    return result


class Device:
    async def wallpaper(self, device_id):
        from pymobiledevice3.services.springboard import SpringBoardServicesService
        async with self.phone(device_id) as phone:
            async with SpringBoardServicesService(phone) as service:
                data = await asyncio.wait_for(service.get_wallpaper_pngdata(), 10)
                return data if isinstance(data, bytes) and data.startswith(b'\x89PNG\r\n\x1a\n') else None

    async def icons(self, device_id, identifiers):
        from pymobiledevice3.services.springboard import SpringBoardServicesService
        result, unavailable = {}, []
        async with self.phone(device_id) as phone:
            async with SpringBoardServicesService(phone) as service:
                for identifier in identifiers:
                    try:
                        data = await asyncio.wait_for(service.get_icon_pngdata(identifier), 5)
                        if not isinstance(data, bytes) or not data.startswith(b'\x89PNG\r\n\x1a\n'):
                            raise ValueError('Icon unavailable')
                        result[identifier] = data
                    except (ValueError, asyncio.TimeoutError):
                        unavailable.append(identifier)
        return result, unavailable

    async def devices(self):
        from pymobiledevice3.usbmux import list_devices
        from pymobiledevice3.lockdown import create_using_usbmux
        result = []
        for d in await list_devices():
            item = {'id': d.serial, 'connection': str(d.connection_type), 'name': 'iPhone', 'available': False}
            try:
                async with await create_using_usbmux(serial=d.serial, autopair=False) as phone:
                    item.update(name=phone.all_values.get('DeviceName', 'iPhone'), model=phone.all_values.get('ProductType'),
                                ios=phone.all_values.get('ProductVersion'), available=bool(phone.paired))
            except Exception as e:
                item['error'] = str(e)
            result.append(item)
        return result

    @contextlib.asynccontextmanager
    async def phone(self, device_id):
        if not isinstance(device_id, str) or not device_id or len(device_id) > 100:
            raise ValueError('Select a connected device first.')
        from pymobiledevice3.lockdown import create_using_usbmux
        async with await create_using_usbmux(serial=device_id, autopair=False) as phone:
            if not phone.paired:
                raise ValueError('Unlock the iPhone and trust this computer in Apple Devices first.')
            yield phone

    async def layout(self, device_id):
        from pymobiledevice3.services.springboard import SpringBoardServicesService
        async with self.phone(device_id) as phone:
            async with SpringBoardServicesService(phone) as service:
                return await service.get_icon_state()

    async def apps(self, device_id, sizes=False):
        from pymobiledevice3.services.installation_proxy import InstallationProxyService
        async with self.phone(device_id) as phone:
            async with InstallationProxyService(phone) as service:
                return await service.get_apps(calculate_sizes=sizes, show_placeholders=True)

    async def write_layout(self, device_id, state):
        from pymobiledevice3.services.springboard import SpringBoardServicesService
        async with self.phone(device_id) as phone:
            async with SpringBoardServicesService(phone) as service:
                await service.set_icon_state(state)
                await asyncio.sleep(1)
                return await service.get_icon_state()

    async def uninstall(self, device_id, identifier):
        from pymobiledevice3.services.installation_proxy import InstallationProxyService
        async with self.phone(device_id) as phone:
            async with InstallationProxyService(phone) as service:
                await service.uninstall(identifier)

