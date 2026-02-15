"""
Typemap Templates.

Perforce typemaps control how files are stored (text, binary, compressed, etc.)
based on file extension. Getting this wrong is expensive:
- Text files stored as binary waste space and lose diff/merge capability
- Binary files stored as text get corrupted
- Large assets without +S (lazy copy) waste storage across streams

Every studio needs typemaps tuned for their pipeline, but most just use the
defaults (which are terrible for creative workflows). This module provides
curated typemap templates for common entertainment pipelines.

Usage:
    manager = TypemapManager(p4_conn)

    # List available templates
    templates = manager.list_templates()

    # Preview what a template would add
    report = manager.preview("vfx")

    # Apply a template (merges with existing typemap)
    manager.apply("vfx", dry_run=False)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from p4admin.core.connection import P4Connection
from p4admin.core.output import ReportData

logger = logging.getLogger(__name__)


# Curated typemap entries for different pipeline types.
# Format: (p4_type, pattern)
# Using +S for lazy copy on large binaries (critical for stream performance).
# Using +C for compressed storage where it helps.
# Using +X for executable scripts.

TYPEMAP_TEMPLATES: dict[str, dict[str, list[tuple[str, str]]]] = {
    "vfx": {
        "description": "VFX & Animation pipeline (Maya, Nuke, Houdini, USD, EXR, Alembic)",
        "entries": [
            # Scene files
            ("binary+S", "//....ma"),       # Maya ASCII (large, but binary for safety)
            ("binary+S", "//....mb"),       # Maya Binary
            ("binary+S", "//....hip"),      # Houdini
            ("binary+S", "//....hipnc"),    # Houdini Non-Commercial
            ("binary+S", "//....hda"),      # Houdini Digital Asset
            ("binary+S", "//....nk"),       # Nuke script
            ("binary+S", "//....nknc"),     # Nuke Non-Commercial
            ("binary+S", "//....blend"),    # Blender
            ("binary+S", "//....c4d"),      # Cinema 4D
            ("binary+S", "//....max"),      # 3ds Max
            ("binary+S", "//....ztl"),      # ZBrush
            ("binary+S", "//....zpr"),      # ZBrush Project

            # USD (Universal Scene Description)
            ("binary+S", "//....usd"),
            ("text", "//....usda"),         # ASCII USD
            ("binary+S", "//....usdc"),     # Crate USD (binary)
            ("binary+S", "//....usdz"),     # USD Zip archive

            # Image formats
            ("binary+S", "//....exr"),      # OpenEXR
            ("binary+S", "//....dpx"),      # DPX film scan
            ("binary+S", "//....hdr"),      # HDR
            ("binary+S", "//....tif"),      # TIFF
            ("binary+S", "//....tiff"),
            ("binary+S", "//....psd"),      # Photoshop
            ("binary+S", "//....psb"),      # Photoshop Big
            ("binary+S", "//....png"),
            ("binary+S", "//....jpg"),
            ("binary+S", "//....jpeg"),
            ("binary+S", "//....tga"),      # Targa
            ("binary+S", "//....bmp"),
            ("binary+S", "//....gif"),
            ("binary+S", "//....svg"),

            # Geometry/cache formats
            ("binary+S", "//....abc"),      # Alembic
            ("binary+S", "//....obj"),
            ("binary+S", "//....fbx"),
            ("binary+S", "//....stl"),
            ("binary+S", "//....ply"),
            ("binary+S", "//....vdb"),      # OpenVDB
            ("binary+S", "//....bgeo"),     # Houdini geometry
            ("binary+S", "//....bgeo.sc"),

            # Texture
            ("binary+S", "//....tex"),      # PRMan/RenderMan
            ("binary+S", "//....rat"),      # Houdini RAT
            ("binary+S", "//....tx"),       # OIIO texture

            # Video
            ("binary+S", "//....mov"),
            ("binary+S", "//....mp4"),
            ("binary+S", "//....avi"),
            ("binary+S", "//....mxf"),

            # Audio
            ("binary+S", "//....wav"),
            ("binary+S", "//....aif"),
            ("binary+S", "//....aiff"),
            ("binary+S", "//....mp3"),

            # Config/data
            ("text", "//....yaml"),
            ("text", "//....yml"),
            ("text", "//....json"),
            ("text", "//....xml"),
            ("text", "//....toml"),
            ("text+x", "//....sh"),
            ("text+x", "//....py"),
            ("text", "//....mel"),          # Maya MEL
            ("text", "//....vex"),          # Houdini VEX
        ],
    },
    "games": {
        "description": "Game development pipeline (Unreal, Unity, common game asset types)",
        "entries": [
            # Unreal Engine
            ("binary+S", "//....uasset"),
            ("binary+S", "//....umap"),
            ("binary+S", "//....uproject"),
            ("binary+S", "//....uplugin"),

            # Unity
            ("binary+S", "//....unity"),
            ("binary+S", "//....prefab"),
            ("binary+S", "//....asset"),
            ("binary+S", "//....anim"),
            ("binary+S", "//....controller"),
            ("binary+S", "//....mat"),
            ("text", "//....meta"),
            ("text", "//....cs"),

            # Common game assets
            ("binary+S", "//....fbx"),
            ("binary+S", "//....obj"),
            ("binary+S", "//....abc"),
            ("binary+S", "//....glb"),
            ("binary+S", "//....gltf"),
            ("binary+S", "//....dae"),

            # Textures
            ("binary+S", "//....psd"),
            ("binary+S", "//....png"),
            ("binary+S", "//....jpg"),
            ("binary+S", "//....jpeg"),
            ("binary+S", "//....tga"),
            ("binary+S", "//....tif"),
            ("binary+S", "//....tiff"),
            ("binary+S", "//....bmp"),
            ("binary+S", "//....dds"),      # DirectDraw Surface
            ("binary+S", "//....ktx"),      # Khronos Texture
            ("binary+S", "//....ktx2"),
            ("binary+S", "//....hdr"),
            ("binary+S", "//....exr"),

            # Audio
            ("binary+S", "//....wav"),
            ("binary+S", "//....mp3"),
            ("binary+S", "//....ogg"),
            ("binary+S", "//....wem"),      # Wwise
            ("binary+S", "//....bnk"),      # Wwise Bank
            ("binary+S", "//....fev"),      # FMOD Event
            ("binary+S", "//....fsb"),      # FMOD Sound Bank

            # Video
            ("binary+S", "//....mp4"),
            ("binary+S", "//....mov"),
            ("binary+S", "//....bik"),      # Bink Video
            ("binary+S", "//....webm"),

            # Fonts
            ("binary+S", "//....ttf"),
            ("binary+S", "//....otf"),
            ("binary+S", "//....woff"),
            ("binary+S", "//....woff2"),

            # Shaders & code
            ("text", "//....hlsl"),
            ("text", "//....glsl"),
            ("text", "//....ush"),          # Unreal shader header
            ("text", "//....usf"),          # Unreal shader file
            ("text", "//....shader"),
            ("text", "//....cginc"),        # Unity shader include
            ("text+x", "//....py"),
            ("text+x", "//....sh"),
            ("text+x", "//....bat"),
            ("text+x", "//....cmd"),

            # Build artifacts (binary, no lazy copy — rebuilt every time)
            ("binary", "//....dll"),
            ("binary", "//....so"),
            ("binary", "//....dylib"),
            ("binary", "//....exe"),
            ("binary", "//....pdb"),

            # Config
            ("text", "//....yaml"),
            ("text", "//....yml"),
            ("text", "//....json"),
            ("text", "//....xml"),
            ("text", "//....ini"),
            ("text", "//....cfg"),
            ("text", "//....toml"),
            ("text", "//....csv"),
        ],
    },
    "post": {
        "description": "Post-production & Editorial (ProRes, DNxHR, AAF, EDL, OTIO)",
        "entries": [
            # Editorial interchange
            ("binary+S", "//....aaf"),      # Advanced Authoring Format
            ("text", "//....edl"),          # Edit Decision List
            ("text", "//....otio"),         # OpenTimelineIO
            ("text", "//....xml"),          # FCP XML, etc.
            ("binary+S", "//....fcpxml"),
            ("binary+S", "//....prproj"),   # Premiere Pro project
            ("binary+S", "//....aep"),      # After Effects project
            ("binary+S", "//....drp"),      # DaVinci Resolve project

            # Video formats
            ("binary+S", "//....mov"),
            ("binary+S", "//....mp4"),
            ("binary+S", "//....mxf"),
            ("binary+S", "//....avi"),
            ("binary+S", "//....r3d"),      # RED RAW
            ("binary+S", "//....braw"),     # Blackmagic RAW
            ("binary+S", "//....ari"),      # ARRI RAW
            ("binary+S", "//....dpx"),
            ("binary+S", "//....exr"),
            ("binary+S", "//....cin"),      # Cineon

            # Audio
            ("binary+S", "//....wav"),
            ("binary+S", "//....aif"),
            ("binary+S", "//....aiff"),
            ("binary+S", "//....mp3"),
            ("binary+S", "//....aac"),
            ("binary+S", "//....flac"),
            ("binary+S", "//....ptx"),      # Pro Tools session

            # Subtitles / Captions
            ("text", "//....srt"),
            ("text", "//....vtt"),
            ("text", "//....scc"),
            ("text", "//....ass"),
            ("text", "//....ssa"),

            # LUT / Color
            ("text", "//....cube"),         # 3D LUT
            ("text", "//....3dl"),
            ("text", "//....csp"),
            ("binary+S", "//....clf"),      # Common LUT Format
            ("text", "//....ocio"),         # OpenColorIO config

            # Documents
            ("binary+S", "//....pdf"),
            ("binary+S", "//....docx"),
            ("binary+S", "//....xlsx"),

            # Images
            ("binary+S", "//....png"),
            ("binary+S", "//....jpg"),
            ("binary+S", "//....jpeg"),
            ("binary+S", "//....tif"),
            ("binary+S", "//....tiff"),
            ("binary+S", "//....psd"),

            # Scripts
            ("text+x", "//....py"),
            ("text+x", "//....sh"),
            ("text", "//....yaml"),
            ("text", "//....yml"),
            ("text", "//....json"),
        ],
    },
}


class TypemapManager:
    """
    Manages Perforce typemap configurations using curated templates.

    Args:
        conn: Active P4Connection instance.
    """

    def __init__(self, conn: P4Connection):
        self.conn = conn

    def list_templates(self) -> dict[str, str]:
        """Return available template names and descriptions."""
        return {name: tmpl["description"] for name, tmpl in TYPEMAP_TEMPLATES.items()}

    def preview(self, template_name: str) -> ReportData:
        """Preview what a template would add to the current typemap."""
        if template_name not in TYPEMAP_TEMPLATES:
            available = ", ".join(TYPEMAP_TEMPLATES.keys())
            raise ValueError(f"Unknown template '{template_name}'. Available: {available}")

        template = TYPEMAP_TEMPLATES[template_name]
        current_entries = self._get_current_typemap()
        new_entries = self._diff_entries(template["entries"], current_entries)

        report = ReportData(
            title=f"Typemap Preview: {template_name}",
            server_info=self.conn.server_info,
            summary={
                "Template": template_name,
                "Description": template["description"],
                "Template entries": len(template["entries"]),
                "Already in typemap": len(template["entries"]) - len(new_entries),
                "New entries to add": len(new_entries),
                "Current typemap entries": len(current_entries),
            },
        )

        if new_entries:
            report.add_section(
                title="New Entries to Add",
                headers=["Type", "Pattern"],
                rows=[[t, p] for t, p in new_entries],
            )

        return report

    def apply(self, template_name: str, dry_run: bool = True) -> ReportData:
        """
        Apply a typemap template (merges with existing typemap).

        Args:
            template_name: Template to apply (e.g., "vfx", "games", "post").
            dry_run: If True, only preview changes.
        """
        if template_name not in TYPEMAP_TEMPLATES:
            available = ", ".join(TYPEMAP_TEMPLATES.keys())
            raise ValueError(f"Unknown template '{template_name}'. Available: {available}")

        template = TYPEMAP_TEMPLATES[template_name]
        current_entries = self._get_current_typemap()
        new_entries = self._diff_entries(template["entries"], current_entries)

        if not dry_run and new_entries:
            self._apply_entries(new_entries)

        report = ReportData(
            title=f"Typemap Apply: {template_name}" + (" [DRY RUN]" if dry_run else ""),
            server_info=self.conn.server_info,
            summary={
                "Template": template_name,
                "Mode": "Dry run" if dry_run else "Applied",
                "Entries added": len(new_entries),
                "Entries skipped (existing)": len(template["entries"]) - len(new_entries),
            },
        )

        if new_entries:
            report.add_section(
                title="Added Entries" if not dry_run else "Entries to Add",
                headers=["Type", "Pattern"],
                rows=[[t, p] for t, p in new_entries],
            )

        return report

    def _get_current_typemap(self) -> list[tuple[str, str]]:
        """Get the current typemap entries."""
        try:
            result = self.conn.run("typemap", "-o")
            if result:
                spec = result[0] if isinstance(result, list) else result
                # Parse TypeMap field — entries are indexed as TypeMap0, TypeMap1, etc.
                entries = []
                idx = 0
                while True:
                    entry = spec.get(f"TypeMap{idx}")
                    if entry is None:
                        break
                    parts = entry.strip().split(None, 1)
                    if len(parts) == 2:
                        entries.append((parts[0], parts[1]))
                    idx += 1
                return entries
        except Exception as e:
            logger.warning("Could not read current typemap: %s", e)
        return []

    def _diff_entries(
        self,
        template_entries: list[tuple[str, str]],
        current_entries: list[tuple[str, str]],
    ) -> list[tuple[str, str]]:
        """Find entries in template that aren't in the current typemap."""
        current_set = {(t.strip(), p.strip()) for t, p in current_entries}
        return [(t, p) for t, p in template_entries if (t.strip(), p.strip()) not in current_set]

    def _apply_entries(self, new_entries: list[tuple[str, str]]):
        """Append new entries to the typemap."""
        try:
            result = self.conn.run("typemap", "-o")
            spec = result[0] if isinstance(result, list) else result

            # Find the next available index
            idx = 0
            while f"TypeMap{idx}" in spec:
                idx += 1

            # Append new entries
            for entry_type, pattern in new_entries:
                spec[f"TypeMap{idx}"] = f"{entry_type} {pattern}"
                idx += 1

            self.conn.run("typemap", "-i", input=spec)
            logger.info("Added %d typemap entries", len(new_entries))

        except Exception as e:
            logger.error("Failed to update typemap: %s", e)
            raise
