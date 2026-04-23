"""NIUA — Unreal Engine plugin bootstrap.

UE auto-executes this file at editor startup when the plugin is enabled
and the Python Editor Script Plugin is on. We just import the real module
so its globals live in sys.modules (menu callbacks reference it by name),
then register the editor menu.
"""

import traceback

try:
    import niua_plugin
    niua_plugin.register_menu()
    print("[NIUA] Plugin loaded — find it under Tools > NIUA")
except Exception:
    # Editor startup shouldn't be blocked by plugin errors; log and move on.
    print("[NIUA] Failed to load:")
    traceback.print_exc()
