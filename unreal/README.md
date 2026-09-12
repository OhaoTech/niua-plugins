# NIUA — Unreal Engine plugin

Paste an `/import/r2/<key>` link from the NIUA web app and import the asset
into your UE project. Same loop as the Blender plugin, built as a pure-Python
UE content plugin — no per-engine-version C++ build required.

## What it imports

| From web | Arrives as |
| --- | --- |
| Image (`.png` / `.jpg` / `.webp`) | `Texture2D` |
| 3D mesh (`.glb`) | `StaticMesh` (or `SkeletalMesh` if rigged) |
| Rigged mesh (`.glb` with bones) | `SkeletalMesh` + skeleton |
| Motion capture (`.bvh` link) | `SkeletalMesh` + `AnimSequence` — the plugin auto-fetches the sibling `.fbx` since UE cannot import BVH directly |
| Music (`.wav`) | `SoundWave` |

UE's importer decides `StaticMesh` vs `SkeletalMesh` vs `AnimSequence` from
the file itself — we don't guess.

## Install

1. **Enable prerequisites** (one-time per UE install). In UE: `Edit > Plugins`
   and enable:
   - **Python Editor Script Plugin**
   - **Editor Scripting Utilities**

2. **Install NIUA**. Copy this `plugins/unreal/` folder (the one containing
   `NIUA.uplugin`) into `<YourProject>/Plugins/NIUA/`. Your layout should be:

   ```
   <YourProject>/
     Plugins/
       NIUA/
         NIUA.uplugin
         Content/
           Python/
             init_unreal.py
             niua_plugin.py
   ```

3. **Restart Unreal.** On startup the Output Log should show
   `[NIUA] Plugin loaded — find it under Tools > NIUA`.

4. **Set your API token.** `Tools > NIUA > Set API Token`. This opens
   `~/.niua/config.json` — paste your token into the `api_token` field:

   ```json
   {
     "api_url": "https://api.ohao.tech",
     "api_token": "mg_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
   }
   ```

   Get a token from <https://ohao.tech/niua/settings> → Developer.

5. **Verify.** `Tools > NIUA > Test Connection` — should show
   `Connected — token valid · $X.XX`.

## Use

1. Generate or browse an asset on <https://ohao.tech/niua/chat> or
   `/assets`.
2. Click **Send to Blender / UE** on the preview, modal previewer, or asset
   vault card — this copies an `/import/r2/…` link to your clipboard.
3. In Unreal: `Tools > NIUA > Import from Clipboard`.
4. The asset lands under `/Game/NIUA/Imports/` by default. Open the Content
   Browser and drag it into your scene.

Legacy `/import/job_<uuid>` links still work.

## Troubleshooting

- **"No API token"** → run `Tools > NIUA > Set API Token` and paste one.
- **"Token rejected"** → token expired or was revoked; generate a new one.
- **"Forbidden"** → the API key's user doesn't own that asset (wrong account).
- **"TLS handshake failed"** → UE's bundled Python couldn't negotiate TLS with
  the R2 host. Open `Window > Developer Tools > Output Log` and look for a
  line starting `[NIUA] Download failed:` — copy that and file an issue.
- **Menu missing after install** → ensure the Python Editor Script Plugin is
  enabled, then fully restart UE (not just reload).

## File layout

- `NIUA.uplugin` — plugin descriptor; declares us as a content-only plugin
  and auto-enables the Python Editor Script and Editor Scripting Utilities
  prerequisites.
- `Content/Python/init_unreal.py` — UE runs this at editor startup when the
  plugin is enabled. Imports `niua_plugin` and registers the menu.
- `Content/Python/niua_plugin.py` — the whole plugin: HTTP client, URL
  parser, clipboard reader, import router, menu commands.

Version 1.0.0.

## License

MIT — see [LICENSE](../LICENSE). Copyright © 2026 Ohao Tech.
