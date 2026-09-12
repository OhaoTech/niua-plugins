"""NIUA — Import AI-generated assets into Blender (v2.0.0)

Generate on the web workbench at ohao.tech/niua, where chat + preview +
variation work properly. Paste the "Send to Blender" link into this plugin
to bring the finished asset into your scene.

Supported asset pipelines (auto-detected from the link):
  image       → loaded into Image Editor
  mesh / rig  → imported as GLB into the scene
  text2motion → imported as BVH armature
  motion      → imported as BVH armature
  music       → saved to a temp path (drop into your DAW)

Get an API token at https://ohao.tech/niua/settings → Developer tab.
"""

bl_info = {
    "name": "NIUA - Import AI Assets",
    "author": "Ohao Tech",
    "version": (2, 1, 2),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > NIUA",
    "description": "Import AI-generated game assets from ohao.tech/niua. Paste an asset link and press Import.",
    "category": "3D View",
    "doc_url": "https://ohao.tech/niua/docs",
}

import bpy
import os
import re
import ssl
import json
import threading
import tempfile
import webbrowser
from bpy.props import StringProperty
from urllib.request import Request, urlopen
from urllib.parse import urljoin

DEFAULT_API_URL = "https://api.ohao.tech"
DEFAULT_WEB_URL = "https://ohao.tech/niua"
# Production / retired API hosts map to the product path, not the apex.
# Custom `api.` hosts still strip the subdomain so local/staging UIs work.
_CANONICAL_API_HOSTS = ("api.ohao.tech", "api.niua.ohao.tech", "niua.ohao.tech")


def _web_base_from_api(api_url):
    """Derive the product web origin from an API base URL."""
    api = (api_url or DEFAULT_API_URL).rstrip("/")
    host = api.split("://", 1)[-1]
    if host in _CANONICAL_API_HOSTS:
        return DEFAULT_WEB_URL
    web = api.replace("https://api.", "https://").replace("http://api.", "http://")
    if not web or web == api:
        return DEFAULT_WEB_URL
    return web


# ── Preferences ──────────────────────────────────────────────────────

class NIUAPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    api_url: StringProperty(
        name="API URL",
        description="NIUA API base URL",
        default=DEFAULT_API_URL,
    )

    api_token: StringProperty(
        name="API Token",
        description="Your NIUA API token (Settings → Developer on the web app)",
        subtype='PASSWORD',
    )

    test_result: StringProperty(default="")

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "api_url")
        layout.prop(self, "api_token")
        row = layout.row(align=True)
        row.operator("niua.test_connection", icon='LINKED')
        row.operator("niua.open_settings", text="Get API Token (Web)", icon='URL')
        if self.test_result:
            box = layout.box()
            r = box.row()
            r.alert = self.test_result.startswith(("✗", "⚠"))
            r.label(text=self.test_result)


# ── HTTP Client ──────────────────────────────────────────────────────

def _build_ssl_context():
    """Build a TLS context that survives Blender's bundled Python on every
    platform. Tries certifi (if it happens to be bundled), then falls back to
    the stdlib default which reads the OS trust store. The two calls exist
    because some Blender builds ship a stripped Python that can't find its
    own cert bundle, and we'd rather trust certifi than fail the handshake.
    """
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


_SSL_CONTEXT = _build_ssl_context()

# Blender's Python ships as "Python-urllib/3.x", which Cloudflare (in front
# of R2) sometimes rejects with a 403. We send a regular browser UA instead.
_USER_AGENT = (
    f"NIUA-Blender/{bl_info['version'][0]}.{bl_info['version'][1]}.{bl_info['version'][2]} "
    f"(+{DEFAULT_WEB_URL})"
)


class NIUAClient:
    """Thin HTTP wrapper around the NIUA API."""

    @staticmethod
    def get_prefs():
        return bpy.context.preferences.addons[__name__].preferences

    @staticmethod
    def request(method, path, data=None, timeout=300):
        prefs = NIUAClient.get_prefs()
        if not prefs.api_token.strip():
            raise RuntimeError("Missing API token — open Edit > Preferences > Add-ons > NIUA")
        url = urljoin(prefs.api_url.rstrip("/") + "/", path.lstrip("/"))
        headers = {
            "x-api-key": prefs.api_token,
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        }
        body = json.dumps(data).encode() if data else None
        req = Request(url, data=body, headers=headers, method=method)
        with urlopen(req, timeout=timeout, context=_SSL_CONTEXT) as resp:
            return json.loads(resp.read())

    @staticmethod
    def get(path):
        return NIUAClient.request("GET", path)

    @staticmethod
    def _fetch_to_temp(url, suggested_ext=".bin"):
        # Presigned R2 URLs are signed GETs; no auth headers, but they do go
        # through a different host (cloudflarestorage.com or the public R2
        # domain) than our gateway, so they exercise the TLS path afresh —
        # hence the explicit context. We also send a browser-like User-Agent
        # because Cloudflare in front of R2 sometimes blocks the default
        # "Python-urllib/3.x" UA with a 403.
        tmp = tempfile.NamedTemporaryFile(suffix=suggested_ext, delete=False)
        req = Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urlopen(req, timeout=120, context=_SSL_CONTEXT) as resp:
                tmp.write(resp.read())
        finally:
            tmp.close()
        return tmp.name

    @staticmethod
    def download_by_key(r2_key):
        """Account-bound download by R2 key. Server enforces ownership from
        the key prefix and returns a presigned URL. Returns local_path."""
        meta = NIUAClient.get(f"/api/download/r2/{r2_key}")
        url = meta.get("url", "")
        key = meta.get("key", r2_key)
        if not url:
            return None
        ext = os.path.splitext(key)[1] or ".bin"
        return NIUAClient._fetch_to_temp(url, ext)

    @staticmethod
    def download_job(job_id):
        """Legacy jobId-based download. Kept so already-copied /import/job_*
        URLs keep working; new URLs use the key path instead."""
        meta = NIUAClient.get(f"/api/download/job/{job_id}")
        url = meta.get("url", "")
        key = meta.get("key", "")
        if not url:
            return None
        ext = os.path.splitext(key)[1] or ".bin"
        return NIUAClient._fetch_to_temp(url, ext)


# ── URL Parser ───────────────────────────────────────────────────────

_UUID_RE = re.compile(
    r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})',
    re.IGNORECASE,
)

_IMPORT_R2_RE = re.compile(r'/import/r2/(.+?)(?:[?#]|$)', re.IGNORECASE)

def parse_asset_ref(text):
    """Parse whatever the user pasted and return one of:
        ("r2",  "<r2-key-with-slashes>")
        ("job", "<uuid>")
        None
    Accepted shapes:
      - https://ohao.tech/niua/import/r2/<key>   (primary)
      - /import/r2/<key>
      - https://ohao.tech/niua/import/job_<uuid> (legacy)
      - /import/job_<uuid> / job_<uuid>
      - Bare UUID
    """
    if not text:
        return None
    stripped = text.strip()
    m = _IMPORT_R2_RE.search(stripped)
    if m:
        return ("r2", m.group(1).strip().rstrip('/'))
    m = _UUID_RE.search(stripped)
    if m:
        return ("job", m.group(1))
    return None


# ── Status ───────────────────────────────────────────────────────────

def _set_status_main_thread(msg):
    try:
        bpy.context.scene.niua.status_msg = msg
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
    except Exception:
        pass
    return None

def set_status(msg):
    bpy.app.timers.register(lambda: _set_status_main_thread(msg), first_interval=0.0)


# ── Import helpers ───────────────────────────────────────────────────

def _import_by_extension(path):
    """Route a downloaded file to the right Blender importer using the file
    extension. With R2-key URLs we always have a real filename, so extension
    is the single source of truth for routing."""
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.png', '.jpg', '.jpeg', '.webp'):
        img = bpy.data.images.load(path)
        img.name = f"NIUA: {os.path.basename(path)}"
        for area in bpy.context.screen.areas:
            if area.type == 'IMAGE_EDITOR':
                area.spaces.active.image = img
                break
        return f"Image loaded — check Image Editor"
    if ext in ('.glb', '.gltf'):
        bpy.ops.import_scene.gltf(filepath=path)
        return f"GLB imported — check the scene outliner"
    if ext == '.bvh':
        bpy.ops.import_anim.bvh(filepath=path, global_scale=0.01, frame_start=1)
        return f"BVH imported — play the timeline"
    if ext in ('.wav', '.mp3', '.ogg', '.flac'):
        return f"Audio saved to {path} — drop it onto the VSE or into your DAW"
    return f"Saved to {path} (no matching importer for {ext or 'unknown type'})"


# ── Operators ────────────────────────────────────────────────────────

def _redraw_preferences():
    for area in bpy.context.screen.areas:
        if area.type == 'PREFERENCES':
            area.tag_redraw()

def _set_pref_result(msg):
    def _apply():
        try:
            NIUAClient.get_prefs().test_result = msg
            _redraw_preferences()
        except Exception:
            pass
        return None
    bpy.app.timers.register(_apply, first_interval=0.0)


class NIUA_OT_OpenSettings(bpy.types.Operator):
    bl_idname = "niua.open_settings"
    bl_label = "Open NIUA Settings"
    bl_description = "Open the NIUA web settings page to manage API tokens"

    def execute(self, context):
        prefs = NIUAClient.get_prefs()
        web = _web_base_from_api(prefs.api_url)
        webbrowser.open(f"{web}/settings")
        self.report({'INFO'}, f"Opened {web}/settings")
        return {'FINISHED'}


class NIUA_OT_OpenWebApp(bpy.types.Operator):
    bl_idname = "niua.open_web_app"
    bl_label = "Open NIUA Web App"
    bl_description = "Open the NIUA workbench — generate assets, chat, iterate on the web"

    def execute(self, context):
        prefs = NIUAClient.get_prefs()
        web = _web_base_from_api(prefs.api_url)
        webbrowser.open(f"{web}/chat")
        return {'FINISHED'}


class NIUA_OT_TestConnection(bpy.types.Operator):
    bl_idname = "niua.test_connection"
    bl_label = "Test Connection"
    bl_description = "Verify the API is reachable and the token is valid"

    def execute(self, context):
        _set_pref_result("Testing…")

        def _test():
            try:
                result = NIUAClient.get("/api/wallet/status")
                bal = result.get("wallet_display") or "balance unknown"
                _set_pref_result(f"✓ Connected — token valid · {bal}")
            except Exception as e:
                err = str(e)
                if "401" in err or "Unauthoriz" in err.lower():
                    _set_pref_result("✗ Token rejected (401) — paste a fresh token from /settings → Developer")
                elif "Missing API token" in err:
                    _set_pref_result("✗ No API token set — paste one above")
                else:
                    _set_pref_result(f"✗ {err}")

        threading.Thread(target=_test, daemon=True).start()
        self.report({'INFO'}, "Testing connection…")
        return {'FINISHED'}


class NIUA_OT_Import(bpy.types.Operator):
    bl_idname = "niua.import_asset"
    bl_label = "Import"
    bl_description = "Download and import the asset from the pasted NIUA link"

    def execute(self, context):
        props = context.scene.niua
        parsed = parse_asset_ref(props.import_url)
        if not parsed:
            self.report({'WARNING'}, "Paste a NIUA import link first")
            return {'CANCELLED'}

        kind, value = parsed
        short = os.path.basename(value) if kind == "r2" else value[:8]
        set_status(f"Importing {short}…")

        def worker():
            try:
                path = (
                    NIUAClient.download_by_key(value)
                    if kind == "r2"
                    else NIUAClient.download_job(value)
                )
            except Exception as e:
                err = str(e)
                # Also surface to Blender's Info area and stdout — the status
                # line truncates long messages (SSL errors routinely overflow),
                # so we print full detail for copy-paste debugging.
                print(f"NIUA import error: {err}", flush=True)
                if "403" in err:
                    set_status("✗ You don't own this asset — check you're logged in as the right user")
                elif "404" in err:
                    set_status("✗ Asset not found — it may still be processing, or the link is stale")
                elif "401" in err:
                    set_status("✗ Token rejected — update it in add-on preferences")
                elif "SSL" in err or "ssl" in err:
                    set_status(f"✗ TLS handshake failed — see Window > Toggle System Console for details")
                else:
                    set_status(f"✗ Import failed: {e}")
                return

            if not path:
                set_status("✗ No file returned — try again in a moment")
                return

            def main_thread():
                try:
                    msg = _import_by_extension(path)
                    _set_status_main_thread(f"✓ {msg}")
                except Exception as e:
                    _set_status_main_thread(f"✗ Import error: {e}")
                return None
            bpy.app.timers.register(main_thread, first_interval=0.0)

        threading.Thread(target=worker, daemon=True).start()
        return {'FINISHED'}


class NIUA_OT_PasteFromClipboard(bpy.types.Operator):
    bl_idname = "niua.paste_clipboard"
    bl_label = "Paste"
    bl_description = "Paste the import link from clipboard"

    def execute(self, context):
        text = context.window_manager.clipboard or ""
        if not text.strip():
            self.report({'WARNING'}, "Clipboard is empty")
            return {'CANCELLED'}
        context.scene.niua.import_url = text.strip()
        return {'FINISHED'}


# ── Properties ───────────────────────────────────────────────────────

class NIUAProperties(bpy.types.PropertyGroup):
    import_url: StringProperty(
        name="NIUA Link",
        description="Paste a link copied from ohao.tech/niua (the 'Send to Blender' button)",
        default="",
    )
    status_msg: StringProperty(name="Status", default="")


# ── Panel ────────────────────────────────────────────────────────────

class NIUA_PT_MainPanel(bpy.types.Panel):
    bl_label = "NIUA"
    bl_idname = "NIUA_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "NIUA"

    def draw(self, context):
        layout = self.layout
        props = context.scene.niua

        # Status banner
        if props.status_msg:
            row = layout.row()
            row.alert = props.status_msg.startswith(("✗", "⚠"))
            row.label(text=props.status_msg)

        # Import section — the core of the plugin
        box = layout.box()
        box.label(text="Import Asset", icon='IMPORT')
        box.prop(props, "import_url", text="")
        row = box.row(align=True)
        row.operator("niua.paste_clipboard", icon='PASTEDOWN')
        row.operator("niua.import_asset", icon='PLAY')

        # Help: go to web app to generate
        layout.separator()
        layout.label(text="Generate assets on the web:", icon='WORLD')
        layout.operator("niua.open_web_app", text="Open NIUA Workbench", icon='URL')

        # Settings shortcut
        layout.separator()
        layout.operator("niua.open_settings", text="Manage API Token", icon='PREFERENCES')


# ── Registration ─────────────────────────────────────────────────────

classes = (
    NIUAPreferences,
    NIUAProperties,
    NIUA_OT_OpenSettings,
    NIUA_OT_OpenWebApp,
    NIUA_OT_TestConnection,
    NIUA_OT_Import,
    NIUA_OT_PasteFromClipboard,
    NIUA_PT_MainPanel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.niua = bpy.props.PointerProperty(type=NIUAProperties)
    print("NIUA 2.0.0 registered — import-only")


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.niua
    print("NIUA unregistered")


if __name__ == "__main__":
    register()
