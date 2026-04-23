# NIUA — engine plugins

Import bridges that pull AI-generated assets from [NIUA](https://niua.ohao.tech)
into your DCC tool. Generate on the web, click **Send to Blender / UE** to copy
an `/import/r2/<key>` URL, paste-and-import in your editor.

| Plugin | Latest | Install |
| --- | --- | --- |
| **Blender** (4.2+) | [blender-plugin-v2.1.2](https://github.com/OhaoTech/niua-plugins/releases/tag/blender-plugin-v2.1.2) | Edit → Preferences → Add-ons → Install... → pick the zip |
| **Unreal Engine** (5.x) | [unreal-plugin-v1.0.0](https://github.com/OhaoTech/niua-plugins/releases/tag/unreal-plugin-v1.0.0) | Unzip into `<YourProject>/Plugins/` so the layout becomes `<YourProject>/Plugins/NIUA/NIUA.uplugin` |

Each plugin's folder has its own `README.md` with full install + usage steps.

## Get an API token

1. Sign in at <https://niua.ohao.tech/login>
2. <https://niua.ohao.tech/settings> → Developer → create a key
3. Paste the token into the plugin's preferences (Blender) or `~/.niua/config.json` (Unreal)

## What the plugins import

| From the web app | Lands in Blender as | Lands in UE as |
| --- | --- | --- |
| Image (`.png` / `.jpg` / `.webp`) | Image data-block | `Texture2D` |
| 3D mesh (`.glb`) | Imported scene with materials | `StaticMesh` (`SkeletalMesh` if rigged) |
| Rigged mesh | Imported scene with armature | `SkeletalMesh` + skeleton |
| Motion (`.bvh`) | Action on a new armature | `SkeletalMesh` + `AnimSequence` (auto-fetches sibling FBX since UE can't import BVH) |
| Music (`.wav`) | (download only) | `SoundWave` |

## Build a plugin from source

Each plugin has its own `build.sh`:

```bash
cd blender && ./build.sh   # produces dist/niua-blender-<version>.zip
cd unreal  && ./build.sh   # produces dist/niua-unreal-<version>.zip
```

## Layout

- `blender/` — single-file Blender add-on (`__init__.py`)
- `unreal/`  — content-only UE plugin (`NIUA.uplugin` + `Content/Python/`)

## License

See repository root.
