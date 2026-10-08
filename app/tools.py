def schema(properties=None, required=None):
    return {'type': 'object', 'properties': properties or {}, 'required': required or [], 'additionalProperties': False}

TEXT = {'type': 'string'}
BOOL = {'type': 'boolean'}
DEVICE = {'device_id': TEXT}
TOOLS = []


def tool(name, description, properties=None, required=None, write=False):
    TOOLS.append({'name': name, 'description': description, 'inputSchema': schema(properties, required),
                  'annotations': {'readOnlyHint': not write, 'destructiveHint': write, 'idempotentHint': not write, 'openWorldHint': True}})


tool('devices.list', 'List connected iPhones and pairing status.')
tool('apps.icons', 'Read actual app icons from the iPhone as PNG data URLs. Batch up to 32 Identifiers; refresh bypasses the memory cache.', {**DEVICE, 'identifiers': {'type': 'array', 'items': TEXT, 'minItems': 1, 'maxItems': 32}, 'refresh': BOOL}, ['device_id', 'identifiers'])
tool('apps.list', 'List installed apps by Identifier. Optionally include app/data storage sizes.', {**DEVICE, 'calculate_sizes': BOOL}, ['device_id'])
tool('layout.read', 'Read the current dock, pages, folders, unique icon keys and revision.', DEVICE, ['device_id'])
tool('layout.wallpaper', 'Read Home Screen wallpaper as a PNG data URL when supported by the connected iPhone. Reports unavailable if the phone does not expose it.', DEVICE, ['device_id'])
tool('layout.plan', 'Stage a full layout without writing to the phone. Use layout.read output; preserve every icon exactly once. Page one and dock are protected by default.',
     {**DEVICE, 'base_revision': TEXT, 'layout': {'type': 'object', 'properties': {'dock': {'type': 'array'}, 'pages': {'type': 'array'}}, 'required': ['dock', 'pages']}, 'protect_page_one': BOOL, 'protect_dock': BOOL}, ['device_id', 'base_revision', 'layout'])
tool('layout.move', 'Stage moving one icon by unique icon key to a one-based page and position. Does not apply changes. Page one and dock remain protected by default.',
     {**DEVICE, 'icon_key': TEXT, 'page': {'type': 'integer', 'minimum': 1}, 'position': {'type': 'integer', 'minimum': 1}, 'protect_page_one': BOOL, 'protect_dock': BOOL}, ['device_id', 'icon_key', 'page'])
tool('apps.plan_delete', 'Preview uninstalling exactly the supplied installed app Identifiers and their local data. Does not uninstall yet.',
     {**DEVICE, 'identifiers': {'type': 'array', 'items': TEXT, 'minItems': 1, 'uniqueItems': True}}, ['device_id', 'identifiers'])
tool('changes.preview', 'Read the exact staged plan and its status.', {'plan_id': TEXT}, ['plan_id'])
tool('changes.list', 'List staged and previously applied plans.')
tool('changes.discard', 'Discard a staged plan without changing the phone.', {'plan_id': TEXT}, ['plan_id'])
tool('changes.apply', 'Apply a reviewed plan ONLY after the human authorizes those exact changes. confirmed must be true. Saves original layout, rejects stale plans, and verifies the saved device result. App deletion cannot be undone by a layout backup.',
     {'plan_id': TEXT, 'confirmed': BOOL}, ['plan_id', 'confirmed'], True)
tool('layouts.list_backups', 'List saved Home Screen layouts, optionally for one device.', DEVICE)
tool('layouts.backup', 'Save a Home Screen layout backup. Does not back up app data.', DEVICE, ['device_id'])
tool('layouts.plan_restore', 'Preview restoring a saved layout to the same phone. Requires the same current icon set; does not restore deleted app data.',
     {**DEVICE, 'backup_id': TEXT, 'protect_page_one': BOOL, 'protect_dock': BOOL}, ['device_id', 'backup_id'])
tool('history.list', 'Read operation outcomes, verification results and backup references.')
tool('choices.read', 'Read saved Keep/Delete choices, keyed by app Identifier.', DEVICE, ['device_id'])
tool('choices.save', 'Save sorting progress and undo history to the portable data folder. Does not uninstall apps.',
     {**DEVICE, 'choices': {'type': 'object', 'additionalProperties': {'enum': ['keep', 'delete']}}, 'history': {'type': 'array'}}, ['device_id', 'choices'])


def validate_arguments(name, args):
    info = next((x for x in TOOLS if x['name'] == name), None)
    if not info:
        raise ValueError('Unknown tool.')
    if not isinstance(args, dict):
        raise ValueError('Arguments must be an object.')
    s = info['inputSchema']
    if set(args) - set(s['properties']) or set(s['required']) - set(args):
        raise ValueError('Missing required or unexpected arguments.')
    for k, value in args.items():
        kind = s['properties'][k].get('type')
        types = {'string': str, 'boolean': bool, 'object': dict, 'array': list, 'integer': int}
        if kind in types and (not isinstance(value, types[kind]) or kind == 'integer' and isinstance(value, bool)):
            raise ValueError('Invalid argument type for ' + k)

