"""NIUA — Unreal Engine import bridge.

Architecture (mirrors the Blender plugin):
  1. User generates an asset on the NIUA web app and clicks "Send to Blender / UE"
     to copy a /import/r2/<r2-key> URL to the clipboard.
  2. In Unreal, Tools > NIUA > Import from Clipboard.
  3. This module reads the clipboard, parses the key, calls the NIUA API to
     exchange the key for a short-lived presigned URL (auth = user's API key
     from ~/.niua/config.json), downloads the bytes, and hands the local file
     to UE's AssetImportTask — routed to the right importer by file extension.

The plugin is Python-only on purpose. UE's Python API lacks a native text input
widget, and a C++ module would require per-engine-version precompiled builds.
Clipboard-based import is one file, zero UE asset dependencies, and matches the
paste-and-go flow users already have in Blender.
"""

import json
import os
import platform
import re
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import unreal


# ── Config ──────────────────────────────────────────────────────────────────

PLUGIN_VERSION = "1.0.0"
DEFAULT_API_URL = "https://api.niua.ohao.tech"
CONFIG_PATH = Path.home() / ".niua" / "config.json"
DEFAULT_IMPORT_DIR = "/Game/NIUA/Imports"

# Blender's Python identifies as "Python-urllib/3.x" and Cloudflare (in front
# of R2) sometimes 403s on that UA. Same treatment for UE — send a branded UA.
USER_AGENT = (
    f"NIUA-Unreal/{PLUGIN_VERSION} "
    f"(+https://niua.ohao.tech; Python/{sys.version_info.major}.{sys.version_info.minor})"
)


def _load_config():
    if not CONFIG_PATH.exists():
        return {"api_url": DEFAULT_API_URL, "api_token": ""}
    try:
        data = json.loads(CONFIG_PATH.read_text())
        data.setdefault("api_url", DEFAULT_API_URL)
        data.setdefault("api_token", "")
        return data
    except Exception:
        return {"api_url": DEFAULT_API_URL, "api_token": ""}


def _save_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


# ── TLS context ─────────────────────────────────────────────────────────────

def _build_ssl_context():
    """Default context reads the OS trust store. Tries certifi first in case
    UE's bundled Python can't find its own cert bundle."""
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


_SSL_CONTEXT = _build_ssl_context()


# ── HTTP client ─────────────────────────────────────────────────────────────

class NIUAClient:
    @staticmethod
    def _get_config_or_raise():
        cfg = _load_config()
        token = cfg.get("api_token", "").strip()
        if not token:
            raise RuntimeError(
                "No API token — run Tools > NIUA > Set API Token and paste one "
                "from https://niua.ohao.tech/settings → Developer."
            )
        return cfg

    @staticmethod
    def get(path, timeout=30):
        cfg = NIUAClient._get_config_or_raise()
        url = urljoin(cfg["api_url"].rstrip("/") + "/", path.lstrip("/"))
        req = Request(url, headers={
            "x-api-key": cfg["api_token"],
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        })
        with urlopen(req, timeout=timeout, context=_SSL_CONTEXT) as resp:
            return json.loads(resp.read())

    @staticmethod
    def _fetch_to_temp(url, suggested_ext=".bin"):
        # Presigned R2 URLs go to a different host than our gateway and carry
        # no auth headers — the signature is the auth. Explicit SSL context +
        # branded UA avoid TLS and WAF flakes we saw in the Blender plugin.
        tmp = tempfile.NamedTemporaryFile(suffix=suggested_ext, delete=False)
        req = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(req, timeout=300, context=_SSL_CONTEXT) as resp:
                tmp.write(resp.read())
        finally:
            tmp.close()
        return tmp.name

    @staticmethod
    def download_by_key(r2_key):
        """Resolve an R2 key to a local file.

        UE cannot import BVH natively; our motion services produce a sibling
        FBX alongside every BVH, so we transparently prefer the FBX variant
        when the link happens to be a BVH. If no FBX exists we fall back to
        the original key and let UE report the unsupported format.
        """
        key_to_fetch = r2_key
        suggested_ext = os.path.splitext(r2_key)[1] or ".bin"

        if r2_key.lower().endswith(".bvh"):
            fbx_candidate = r2_key[:-4] + ".fbx"
            try:
                meta = NIUAClient.get(f"/api/download/r2/{fbx_candidate}")
                if meta.get("url"):
                    return NIUAClient._fetch_to_temp(meta["url"], ".fbx")
            except Exception as e:
                # 404 on the FBX sibling is expected for older jobs; fall
                # through to the original BVH request with a log line.
                print(f"[NIUA] No FBX sibling for {r2_key} ({e}); using BVH.")

        meta = NIUAClient.get(f"/api/download/r2/{key_to_fetch}")
        url = meta.get("url", "")
        if not url:
            return None
        return NIUAClient._fetch_to_temp(url, suggested_ext)

    @staticmethod
    def download_job(job_id):
        """Legacy fetch path for /import/job_<uuid> links already in the wild."""
        meta = NIUAClient.get(f"/api/download/job/{job_id}")
        url = meta.get("url", "")
        key = meta.get("key", "")
        if not url:
            return None
        ext = os.path.splitext(key)[1] or ".bin"
        return NIUAClient._fetch_to_temp(url, ext)


# ── URL / ref parser ────────────────────────────────────────────────────────

_IMPORT_R2_RE = re.compile(r"/import/r2/(.+?)(?:[?#]|$)", re.IGNORECASE)
_UUID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)


def parse_asset_ref(text):
    """Parse whatever the user pasted. Returns one of:
        ("r2",  "<r2-key-with-slashes>")   — new format
        ("job", "<uuid>")                   — legacy /import/job_<uuid>
        None
    """
    if not text:
        return None
    s = text.strip()
    m = _IMPORT_R2_RE.search(s)
    if m:
        return ("r2", m.group(1).strip().rstrip("/"))
    m = _UUID_RE.search(s)
    if m:
        return ("job", m.group(1))
    return None


# ── Clipboard read ──────────────────────────────────────────────────────────

def _read_clipboard():
    """Cross-platform clipboard read. Returns "" on failure rather than raise
    so the caller can show a helpful message instead of a stack trace."""
    system = platform.system()
    try:
        if system == "Windows":
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                text=True, timeout=5,
            )
            return out.strip()
        if system == "Darwin":
            out = subprocess.check_output(["pbpaste"], text=True, timeout=5)
            return out.strip()
        # Linux — try Wayland then X11 clipboards.
        for cmd in (["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"]):
            try:
                out = subprocess.check_output(cmd, text=True, timeout=5)
                return out.strip()
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue
        return ""
    except Exception:
        return ""


# ── UE import routing ───────────────────────────────────────────────────────

# Rough extension → human label map used for status messages only. UE's
# AssetImportTask auto-detects the concrete asset class from the file itself.
_EXT_LABEL = {
    ".png": "Texture2D", ".jpg": "Texture2D", ".jpeg": "Texture2D",
    ".tga": "Texture2D", ".webp": "Texture2D", ".exr": "Texture2D",
    ".glb": "StaticMesh", ".gltf": "StaticMesh",
    ".fbx": "SkeletalMesh / StaticMesh / AnimSequence",
    ".wav": "SoundWave", ".mp3": "SoundWave", ".ogg": "SoundWave", ".flac": "SoundWave",
}


def _import_file(local_path, destination_dir=DEFAULT_IMPORT_DIR):
    """Hand a local file to UE's AssetImportTask.

    UE owns the routing: the importer for a .fbx decides StaticMesh vs
    SkeletalMesh vs AnimSequence based on file contents; we don't guess.
    We do refuse files whose extension UE has no chance with (e.g. .bvh)
    because the resulting error is more confusing than a clear rejection.
    """
    ext = os.path.splitext(local_path)[1].lower()
    if ext == ".bvh":
        return (
            "BVH files aren't importable in UE. Our server produces a sibling "
            ".fbx for every motion job — paste a /import/r2/.../animation.fbx "
            "link instead (or re-run the motion job to regenerate FBX)."
        )
    if ext not in _EXT_LABEL:
        return f"No UE importer for {ext or 'unknown type'} — saved to {local_path}"

    # Ensure target directory exists before UE tries to write into it.
    if not unreal.EditorAssetLibrary.does_directory_exist(destination_dir):
        unreal.EditorAssetLibrary.make_directory(destination_dir)

    task = unreal.AssetImportTask()
    task.filename = local_path
    task.destination_path = destination_dir
    task.automated = True        # no popups
    task.save = True             # persist to disk after import
    task.replace_existing = True

    tools = unreal.AssetToolsHelpers.get_asset_tools()
    tools.import_asset_tasks([task])

    imported = list(task.get_editor_property("imported_object_paths") or [])
    name = os.path.splitext(os.path.basename(local_path))[0]
    if imported:
        return f"Imported '{name}' as {_EXT_LABEL[ext]} → {imported[0]}"
    return f"Imported '{name}' into {destination_dir}"


# ── UI helpers ──────────────────────────────────────────────────────────────

def _notify(msg, kind="info"):
    """Surface status to both the editor notification area and the log, so
    long messages (SSL errors especially) are readable even when the popup
    truncates them."""
    print(f"[NIUA] {msg}", flush=True)
    try:
        if kind == "error":
            unreal.EditorDialog.show_message(
                "NIUA",
                msg,
                unreal.AppMsgType.OK,
                unreal.AppReturnType.OK,
            )
        else:
            # Non-blocking toast via SystemLibrary.print_string — appears in
            # the viewport and log.
            unreal.SystemLibrary.print_string(
                None, f"NIUA: {msg}",
                text_color=unreal.LinearColor(0.2, 0.9, 0.95, 1.0),
                duration=6.0,
            )
    except Exception:
        # Fallback: the print() above already went to the Output Log.
        pass


# ── Commands ────────────────────────────────────────────────────────────────

def import_from_clipboard():
    """Menu command: read clipboard, download, import."""
    text = _read_clipboard()
    if not text:
        _notify(
            "Clipboard is empty (or unreachable from UE). Copy an /import/r2/... "
            "link from the NIUA web app and try again.",
            kind="error",
        )
        return

    parsed = parse_asset_ref(text)
    if not parsed:
        _notify(f"Doesn't look like a NIUA link:\n\n{text[:300]}", kind="error")
        return

    kind, value = parsed
    short = os.path.basename(value) if kind == "r2" else value[:8]
    _notify(f"Downloading {short}…")

    try:
        if kind == "r2":
            path = NIUAClient.download_by_key(value)
        else:
            path = NIUAClient.download_job(value)
    except Exception as e:
        err = str(e)
        print(f"[NIUA] Download failed: {err}", flush=True)
        if "401" in err:
            _notify("Token rejected — update it via Tools > NIUA > Set API Token.", kind="error")
        elif "403" in err:
            _notify("Forbidden — your API key doesn't have access to this asset.", kind="error")
        elif "404" in err:
            _notify("Asset not found — it may still be processing or the link is stale.", kind="error")
        elif "SSL" in err or "ssl" in err:
            _notify(f"TLS handshake failed. Check the Output Log for the full error.", kind="error")
        else:
            _notify(f"Download failed: {err}", kind="error")
        return

    if not path:
        _notify("Server returned no URL for this asset.", kind="error")
        return

    try:
        result = _import_file(path)
        _notify(result)
    except Exception as e:
        print(f"[NIUA] Import error: {e}", flush=True)
        _notify(f"Import error: {e}", kind="error")


def set_api_token():
    """Menu command: open the config file in the OS's default editor so the
    user can paste a token. Simpler than building a Slate dialog, and the
    file doubles as a record of which API URL they're hitting."""
    cfg = _load_config()
    if not CONFIG_PATH.exists():
        _save_config(cfg)

    # Best-effort: use the OS default handler. Falls back to logging the path.
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(str(CONFIG_PATH))  # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.Popen(["open", str(CONFIG_PATH)])
        else:
            subprocess.Popen(["xdg-open", str(CONFIG_PATH)])
    except Exception:
        pass
    _notify(
        f"Edit {CONFIG_PATH} and set 'api_token'. Get one from "
        f"https://niua.ohao.tech/settings → Developer."
    )


def open_web_settings():
    """Menu command: open the NIUA Developer settings page for token copy-paste."""
    cfg = _load_config()
    api = cfg.get("api_url", DEFAULT_API_URL).rstrip("/")
    web = api.replace("https://api.", "https://").replace("http://api.", "http://")
    if web == api:
        web = "https://niua.ohao.tech"
    unreal.SystemLibrary.launch_url(f"{web}/settings")


def test_connection():
    """Menu command: hit /api/wallet/status to prove the token works."""
    try:
        res = NIUAClient.get("/api/wallet/status")
        bal = res.get("wallet_display") or "balance unknown"
        _notify(f"Connected — token valid · {bal}")
    except Exception as e:
        err = str(e)
        print(f"[NIUA] Test failed: {err}", flush=True)
        if "401" in err:
            _notify("Token rejected — set a fresh one via Tools > NIUA > Set API Token.", kind="error")
        else:
            _notify(f"Connection failed: {err}", kind="error")


# ── Menu registration ───────────────────────────────────────────────────────

_MENU_NAME = "LevelEditor.MainMenu.Tools"
_SECTION_NAME = "NIUA"


def register_menu():
    """Add a NIUA section under Tools > NIUA in the main editor menu.

    Uses string-command callbacks (UE requires a string expr rather than a
    Python callable reference) — each entry re-imports this module and calls
    the function. Safe because Python caches modules in sys.modules, so the
    import is effectively a lookup.
    """
    menus = unreal.ToolMenus.get()
    tools = menus.find_menu(_MENU_NAME)
    if not tools:
        print(f"[NIUA] Could not find menu {_MENU_NAME}; NIUA menu not registered.")
        return

    sub_menu = tools.add_sub_menu(_MENU_NAME, _SECTION_NAME, "NIUA", "NIUA")

    def _entry(name, label, tooltip, python):
        entry = unreal.ToolMenuEntry(
            name=name,
            type=unreal.MultiBlockType.MENU_ENTRY,
        )
        entry.set_label(label)
        entry.set_tool_tip(tooltip)
        entry.set_string_command(
            unreal.ToolMenuStringCommandType.PYTHON,
            "",  # custom_type — unused when type is PYTHON
            string=python,
        )
        sub_menu.add_menu_entry("NIUAActions", entry)

    _entry(
        "NIUAImportFromClipboard",
        "Import from Clipboard",
        "Import the asset referenced by the /import/r2/... link currently on your clipboard.",
        "import niua_plugin; niua_plugin.import_from_clipboard()",
    )
    _entry(
        "NIUATestConnection",
        "Test Connection",
        "Verify your API token is valid and show the wallet balance.",
        "import niua_plugin; niua_plugin.test_connection()",
    )
    _entry(
        "NIUASetApiToken",
        "Set API Token…",
        "Open ~/.niua/config.json so you can paste an API token.",
        "import niua_plugin; niua_plugin.set_api_token()",
    )
    _entry(
        "NIUAOpenWebSettings",
        "Open Web Settings",
        "Open the NIUA Developer settings page in your browser.",
        "import niua_plugin; niua_plugin.open_web_settings()",
    )

    menus.refresh_all_widgets()
