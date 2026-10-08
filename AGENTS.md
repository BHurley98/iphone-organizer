# Workspace guidance

This is a portable Windows iPhone organizer. The Python backend and browser UI share one device engine, exposed through local HTTP and an MCP stdio bridge.

- Do not hardcode a device Identifier or a developer's filesystem paths.
- Preserve original icon records, including shortcuts and special icons.
- Keep page one and dock protected by default.
- Stage consequential device actions. Require authorization of the reviewed plan before applying.
- Validate the observed device state immediately before applying a plan.
- Back up layouts before writes and uninstall operations; verify saved state with a fresh device read.
- Do not claim app data can be recovered from a layout backup.
- Serialize all device operations across UI and MCP clients.
- Keep credentials out of logs, source, reports, and distribution archives.
- Do not uninstall real apps or reorganize a user's phone merely to run development tests. Use the simulated device tests; a no-change write can validate the live layout path when authorized.
- Never ship `data/`, personal phone snapshots, or test scratch files in the distribution ZIP.
- Test with the portable runtime. Avoid unneeded frontend frameworks or browser-engine bundles.

