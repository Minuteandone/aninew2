"""
Settings Dialog
Export settings and application preferences
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Callable

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QPushButton, QSpinBox, QDoubleSpinBox,
    QSlider, QCheckBox, QGroupBox, QTabWidget,
    QWidget, QFormLayout, QFrame, QProgressBar, QMessageBox,
    QSizePolicy, QLineEdit, QFileDialog, QListWidget, QPlainTextEdit,
    QInputDialog
)
from PyQt6.QtCore import Qt, QSettings, QThread, QObject, pyqtSignal

from utils.ffmpeg_installer import install_ffmpeg, resolve_ffmpeg_path, get_install_root
from utils.shader_registry import ShaderRegistry, ShaderPreset


class ExportSettings:
    """Container for export settings"""
    
    def __init__(self):
        self.settings = QSettings('MSMAnimationViewer', 'ExportSettings')
        self.load()
    
    def load(self):
        """Load settings from storage"""
        # PNG settings
        self.png_compression = self.settings.value('png/compression', 6, type=int)
        self.png_full_resolution = self.settings.value('png/full_resolution', False, type=bool)
        self.png_full_scale_multiplier = self.settings.value('png/full_scale_multiplier', 1.0, type=float)
        
        # GIF settings
        self.gif_fps = self.settings.value('gif/fps', 15, type=int)
        self.gif_colors = self.settings.value('gif/colors', 256, type=int)
        self.gif_dither = self.settings.value('gif/dither', True, type=bool)
        self.gif_optimize = self.settings.value('gif/optimize', True, type=bool)
        self.gif_loop = self.settings.value('gif/loop', 0, type=int)  # 0 = infinite
        self.gif_scale = self.settings.value('gif/scale', 100, type=int)  # percentage
        
        # MOV settings
        # Default to prores_ks for best Adobe compatibility
        self.mov_codec = self.settings.value('mov/codec', 'prores_ks', type=str)
        self.mov_quality = self.settings.value('mov/quality', 'high', type=str)
        self.mov_include_audio = self.settings.value('mov/include_audio', True, type=bool)
        self.mov_full_resolution = self.settings.value('mov/full_resolution', False, type=bool)
        self.mov_full_scale_multiplier = self.settings.value('mov/full_scale_multiplier', 1.0, type=float)

        # WEBM settings
        self.webm_codec = self.settings.value('webm/codec', 'libvpx-vp9', type=str)
        self.webm_crf = self.settings.value('webm/crf', 28, type=int)
        self.webm_speed = self.settings.value('webm/speed', 4, type=int)
        self.webm_include_audio = self.settings.value('webm/include_audio', True, type=bool)
        self.webm_full_resolution = self.settings.value('webm/full_resolution', False, type=bool)
        self.webm_full_scale_multiplier = self.settings.value('webm/full_scale_multiplier', 1.0, type=float)

        # MP4 settings
        self.mp4_codec = self.settings.value('mp4/codec', 'libx264', type=str)
        self.mp4_crf = self.settings.value('mp4/crf', 18, type=int)
        self.mp4_preset = self.settings.value('mp4/preset', 'medium', type=str)
        self.mp4_bitrate = self.settings.value('mp4/bitrate_kbps', 0, type=int)
        self.mp4_include_audio = self.settings.value('mp4/include_audio', True, type=bool)
        self.mp4_full_resolution = self.settings.value('mp4/full_resolution', False, type=bool)
        self.mp4_full_scale_multiplier = self.settings.value('mp4/full_scale_multiplier', 1.0, type=float)
        self.mp4_pixel_format = self.settings.value('mp4/pixel_format', 'yuv420p', type=str)
        self.mp4_faststart = self.settings.value('mp4/faststart', True, type=bool)

        # Camera/view settings
        self.camera_zoom_to_cursor = self.settings.value('camera/zoom_to_cursor', True, type=bool)
        self.use_barebones_file_browser = self.settings.value('files/use_barebones_browser', False, type=bool)
        self.anchor_debug_logging = self.settings.value('logging/anchor_debug', False, type=bool)
        self.update_source_json_on_save = self.settings.value('save/update_source_json', False, type=bool)
    
        # PSD settings
        self.psd_include_hidden = self.settings.value('psd/include_hidden', False, type=bool)
        self.psd_scale = self.settings.value('psd/scale', 100, type=int)
        self.psd_quality = self.settings.value('psd/quality', 'balanced', type=str)
        self.psd_compression = self.settings.value('psd/compression', 'rle', type=str)
        self.psd_crop_canvas = self.settings.value('psd/crop_canvas', False, type=bool)
        self.psd_match_viewport = self.settings.value('psd/match_viewport', False, type=bool)
        self.psd_preserve_resolution = self.settings.value('psd/preserve_resolution', False, type=bool)
        self.psd_full_res_multiplier = self.settings.value('psd/full_res_multiplier', 1.0, type=float)
    
    def save(self):
        """Save settings to storage"""
        # PNG settings
        self.settings.setValue('png/compression', self.png_compression)
        self.settings.setValue('png/full_resolution', self.png_full_resolution)
        self.settings.setValue('png/full_scale_multiplier', self.png_full_scale_multiplier)
        
        # GIF settings
        self.settings.setValue('gif/fps', self.gif_fps)
        self.settings.setValue('gif/colors', self.gif_colors)
        self.settings.setValue('gif/dither', self.gif_dither)
        self.settings.setValue('gif/optimize', self.gif_optimize)
        self.settings.setValue('gif/loop', self.gif_loop)
        self.settings.setValue('gif/scale', self.gif_scale)
        
        # MOV settings
        self.settings.setValue('mov/codec', self.mov_codec)
        self.settings.setValue('mov/quality', self.mov_quality)
        self.settings.setValue('mov/include_audio', self.mov_include_audio)
        self.settings.setValue('mov/full_resolution', self.mov_full_resolution)
        self.settings.setValue('mov/full_scale_multiplier', self.mov_full_scale_multiplier)

        # WEBM settings
        self.settings.setValue('webm/codec', self.webm_codec)
        self.settings.setValue('webm/crf', self.webm_crf)
        self.settings.setValue('webm/speed', self.webm_speed)
        self.settings.setValue('webm/include_audio', self.webm_include_audio)
        self.settings.setValue('webm/full_resolution', self.webm_full_resolution)
        self.settings.setValue('webm/full_scale_multiplier', self.webm_full_scale_multiplier)

        # MP4 settings
        self.settings.setValue('mp4/codec', self.mp4_codec)
        self.settings.setValue('mp4/crf', self.mp4_crf)
        self.settings.setValue('mp4/preset', self.mp4_preset)
        self.settings.setValue('mp4/bitrate_kbps', self.mp4_bitrate)
        self.settings.setValue('mp4/include_audio', self.mp4_include_audio)
        self.settings.setValue('mp4/full_resolution', self.mp4_full_resolution)
        self.settings.setValue('mp4/full_scale_multiplier', self.mp4_full_scale_multiplier)
        self.settings.setValue('mp4/pixel_format', self.mp4_pixel_format)
        self.settings.setValue('mp4/faststart', self.mp4_faststart)

        self.settings.setValue('camera/zoom_to_cursor', self.camera_zoom_to_cursor)
        self.settings.setValue('files/use_barebones_browser', self.use_barebones_file_browser)
        self.settings.setValue('logging/anchor_debug', self.anchor_debug_logging)
        self.settings.setValue('save/update_source_json', self.update_source_json_on_save)
        
        # PSD settings
        self.settings.setValue('psd/include_hidden', self.psd_include_hidden)
        self.settings.setValue('psd/scale', self.psd_scale)
        self.settings.setValue('psd/quality', self.psd_quality)
        self.settings.setValue('psd/compression', self.psd_compression)
        self.settings.setValue('psd/crop_canvas', self.psd_crop_canvas)
        self.settings.setValue('psd/match_viewport', self.psd_match_viewport)
        self.settings.setValue('psd/preserve_resolution', self.psd_preserve_resolution)
        self.settings.setValue('psd/full_res_multiplier', self.psd_full_res_multiplier)


class FFmpegInstallWorker(QObject):
    """Runs the FFmpeg installer logic off the UI thread."""
    
    statusChanged = pyqtSignal(str)
    progressChanged = pyqtSignal(int)
    finished = pyqtSignal(bool, str)

    def __init__(self):
        super().__init__()
    
    def run(self):
        try:
            exe_path = install_ffmpeg(
                status_callback=self.statusChanged.emit,
                progress_callback=self.progressChanged.emit
            )
            self.finished.emit(True, exe_path)
        except Exception as exc:  # pragma: no cover - handled via UI message
            self.finished.emit(False, str(exc))


class ShaderSettingsWidget(QWidget):
    """UI for editing shader approximation overrides."""

    def __init__(
        self,
        shader_registry: ShaderRegistry,
        game_path: Optional[str] = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.shader_registry = shader_registry
        self._pending_overrides: Dict[str, Dict[str, Any]] = dict(
            shader_registry.get_override_payloads()
        )
        self.current_shader: Optional[str] = None
        self.game_path = Path(game_path) if game_path else None
        self.shader_dir = self._compute_shader_dir()
        self.texture_dir = self._compute_texture_dir()
        self._build_ui()
        self.refresh_shader_list()

    # ------------------------------------------------------------------ helpers
    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        left_panel = QVBoxLayout()
        self.shader_list = QListWidget()
        self.shader_list.currentItemChanged.connect(self._on_shader_selected)
        left_panel.addWidget(self.shader_list)

        self.add_shader_btn = QPushButton("Add Shader Override")
        self.add_shader_btn.clicked.connect(self._add_shader_entry)
        left_panel.addWidget(self.add_shader_btn)
        layout.addLayout(left_panel, 1)

        form_container = QGroupBox("Shader Override")
        form_layout = QFormLayout(form_container)

        self.display_name_edit = QLineEdit()
        form_layout.addRow("Display Name:", self.display_name_edit)

        self.fragment_edit = QLineEdit()
        fragment_row = QHBoxLayout()
        fragment_row.addWidget(self.fragment_edit)
        self.fragment_browse_btn = QPushButton("Browse…")
        self.fragment_browse_btn.clicked.connect(
            lambda: self._browse_file(
                self.fragment_edit,
                "GLSL Files (*.glsl);;All Files (*)",
                self.shader_dir,
            )
        )
        fragment_row.addWidget(self.fragment_browse_btn)
        form_layout.addRow("Fragment Shader:", fragment_row)

        self.vertex_edit = QLineEdit()
        vertex_row = QHBoxLayout()
        vertex_row.addWidget(self.vertex_edit)
        self.vertex_browse_btn = QPushButton("Browse…")
        self.vertex_browse_btn.clicked.connect(
            lambda: self._browse_file(
                self.vertex_edit,
                "GLSL Files (*.glsl);;All Files (*)",
                self.shader_dir,
            )
        )
        vertex_row.addWidget(self.vertex_browse_btn)
        form_layout.addRow("Vertex Shader:", vertex_row)

        self.lut_edit = QLineEdit()
        lut_row = QHBoxLayout()
        lut_row.addWidget(self.lut_edit)
        self.lut_browse_btn = QPushButton("Browse…")
        self.lut_browse_btn.clicked.connect(
            lambda: self._browse_file(
                self.lut_edit,
                "Images (*.png *.jpg *.avif *.dds *.exr);;All Files (*)",
                self.texture_dir,
            )
        )
        lut_row.addWidget(self.lut_browse_btn)
        form_layout.addRow("LUT/Texture:", lut_row)
        self.texture_hint_label = QLabel(self._texture_hint_text())
        self.texture_hint_label.setStyleSheet("color: gray; font-size: 9pt;")
        form_layout.addRow("", self.texture_hint_label)

        color_row = QHBoxLayout()
        self.color_spins: List[QDoubleSpinBox] = []
        labels = ['R', 'G', 'B']
        for label in labels:
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 4.0)
            spin.setSingleStep(0.05)
            spin.setDecimals(3)
            spin.setSuffix(f" {label}")
            spin.setToolTip(f"{label} channel multiplier")
            color_row.addWidget(spin)
            self.color_spins.append(spin)
        form_layout.addRow("Color Scale:", color_row)

        self.alpha_spin = QDoubleSpinBox()
        self.alpha_spin.setRange(0.0, 4.0)
        self.alpha_spin.setSingleStep(0.05)
        self.alpha_spin.setDecimals(3)
        form_layout.addRow("Alpha Scale:", self.alpha_spin)

        self.blend_combo = QComboBox()
        self.blend_combo.addItem("Follow Layer (default)", "")
        self.blend_combo.addItem("Standard", "STANDARD")
        self.blend_combo.addItem("Premult Alpha", "PREMULT_ALPHA")
        self.blend_combo.addItem("Additive", "ADDITIVE")
        self.blend_combo.addItem("Multiply", "MULTIPLY")
        self.blend_combo.addItem("Screen", "SCREEN")
        self.blend_combo.addItem("Inherit", "INHERIT")
        form_layout.addRow("Blend Override:", self.blend_combo)

        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlaceholderText("Optional notes about shader usage or replacement GLSL.")
        self.notes_edit.setFixedHeight(80)
        form_layout.addRow("Notes:", self.notes_edit)

        button_row = QHBoxLayout()
        self.save_btn = QPushButton("Save Override")
        self.save_btn.clicked.connect(self._save_current_override)
        button_row.addWidget(self.save_btn)
        self.reset_btn = QPushButton("Reset to Default")
        self.reset_btn.clicked.connect(self._reset_current_override)
        button_row.addWidget(self.reset_btn)
        self.clear_btn = QPushButton("Clear Override")
        self.clear_btn.clicked.connect(self._clear_current_override)
        button_row.addWidget(self.clear_btn)
        form_layout.addRow(button_row)

        right_panel = QVBoxLayout()
        right_panel.addWidget(form_container)
        right_panel.addStretch()
        layout.addLayout(right_panel, 2)

    # ---------------------------------------------------------------- actions
    def refresh_shader_list(self):
        names = set(name.lower() for name in self.shader_registry.list_shader_names())
        names |= set(self._pending_overrides.keys())
        current = self.current_shader
        self.shader_list.clear()
        for name in sorted(names):
            preset = self._effective_payload(name)
            display = preset.get("display_name", name)
            item_text = f"{display} ({name})" if display.lower() != name else name
            self.shader_list.addItem(item_text)
            self.shader_list.item(self.shader_list.count() - 1).setData(Qt.ItemDataRole.UserRole, name)

        if current:
            for idx in range(self.shader_list.count()):
                item = self.shader_list.item(idx)
                if item.data(Qt.ItemDataRole.UserRole) == current:
                    self.shader_list.setCurrentRow(idx)
                    break
        elif self.shader_list.count():
            self.shader_list.setCurrentRow(0)
        else:
            self._load_shader(None)

    def _on_shader_selected(self, current, previous):
        key = current.data(Qt.ItemDataRole.UserRole) if current else None
        self._load_shader(key)

    def _load_shader(self, shader_name: Optional[str]):
        self.current_shader = shader_name
        enabled = shader_name is not None
        for widget in [
            self.display_name_edit,
            self.fragment_edit,
            self.fragment_browse_btn,
            self.vertex_edit,
            self.vertex_browse_btn,
            self.lut_edit,
            self.lut_browse_btn,
            self.alpha_spin,
            self.blend_combo,
            self.notes_edit,
            self.save_btn,
            self.reset_btn,
            self.clear_btn,
        ] + self.color_spins:
            widget.setEnabled(enabled)
        if not shader_name:
            self.display_name_edit.clear()
            self.fragment_edit.clear()
            self.vertex_edit.clear()
            self.lut_edit.clear()
            self.notes_edit.clear()
            for spin in self.color_spins:
                spin.setValue(1.0)
            self.alpha_spin.setValue(1.0)
            self.blend_combo.setCurrentIndex(0)
            return

        payload = self._effective_payload(shader_name)
        self.display_name_edit.setText(payload.get("display_name", shader_name))
        color = payload.get("color_scale", [1.0, 1.0, 1.0])
        for spin, value in zip(self.color_spins, color):
            spin.setValue(float(value))
        self.alpha_spin.setValue(float(payload.get("alpha_scale", 1.0)))
        self.fragment_edit.setText(payload.get("fragment", ""))
        self.vertex_edit.setText(payload.get("vertex", ""))
        self.lut_edit.setText(payload.get("lut", ""))
        blend_value = payload.get("blend_override", "")
        idx = self.blend_combo.findData(blend_value if blend_value is not None else "")
        if idx == -1:
            idx = 0
        self.blend_combo.setCurrentIndex(idx)
        self.notes_edit.setPlainText(payload.get("notes", ""))

    def _effective_payload(self, shader_name: str) -> Dict[str, Any]:
        base = self.shader_registry.get_default_preset(shader_name)
        payload = base.to_dict() if base else {
            "display_name": shader_name,
            "color_scale": [1.0, 1.0, 1.0],
            "alpha_scale": 1.0
        }
        override = self._pending_overrides.get(shader_name.lower())
        if override:
            payload = dict(payload)
            payload.update(override)
        return payload

    def _base_payload(self, shader_name: str) -> Dict[str, Any]:
        base = self.shader_registry.get_default_preset(shader_name)
        if base:
            return base.to_dict()
        return {"display_name": shader_name, "color_scale": [1.0, 1.0, 1.0], "alpha_scale": 1.0}

    def _browse_file(self, line_edit: QLineEdit, filter_str: str, default_dir: Optional[Path] = None):
        start_dir = line_edit.text().strip()
        if not start_dir and default_dir is not None:
            start_dir = str(default_dir)
        if not start_dir:
            start_dir = str(Path.home())
        filename, _ = QFileDialog.getOpenFileName(self, "Select File", start_dir, filter_str)
        if filename:
            line_edit.setText(filename)

    def _collect_form_payload(self) -> Dict[str, Any]:
        if not self.current_shader:
            return {}
        payload: Dict[str, Any] = {}
        base = self._base_payload(self.current_shader)
        display = self.display_name_edit.text().strip() or self.current_shader
        if display != base.get("display_name"):
            payload["display_name"] = display
        color_values = [spin.value() for spin in self.color_spins]
        if list(map(float, color_values)) != [float(v) for v in base.get("color_scale", [1.0, 1.0, 1.0])]:
            payload["color_scale"] = color_values
        alpha = self.alpha_spin.value()
        if float(alpha) != float(base.get("alpha_scale", 1.0)):
            payload["alpha_scale"] = alpha
        fragment = self.fragment_edit.text().strip()
        if fragment:
            payload["fragment"] = fragment
        vertex = self.vertex_edit.text().strip()
        if vertex:
            payload["vertex"] = vertex
        lut = self.lut_edit.text().strip()
        if lut:
            payload["lut"] = lut
        blend_data = self.blend_combo.currentData()
        if blend_data:
            payload["blend_override"] = blend_data
        notes = self.notes_edit.toPlainText().strip()
        if notes:
            payload["notes"] = notes
        return payload

    def _save_current_override(self):
        if not self.current_shader:
            return
        payload = self._collect_form_payload()
        key = self.current_shader.lower()
        if payload:
            self._pending_overrides[key] = payload
        else:
            self._pending_overrides.pop(key, None)
        self.refresh_shader_list()

    def _reset_current_override(self):
        if not self.current_shader:
            return
        self._pending_overrides.pop(self.current_shader.lower(), None)
        self._load_shader(self.current_shader)

    def _clear_current_override(self):
        if not self.current_shader:
            return
        key = self.current_shader.lower()
        if self.shader_registry.get_default_preset(self.current_shader) is None:
            # Custom entry, remove entirely
            self._pending_overrides.pop(key, None)
            self.current_shader = None
            self.refresh_shader_list()
        else:
            self._pending_overrides.pop(key, None)
            self._load_shader(self.current_shader)

    def _add_shader_entry(self):
        name, ok = QInputDialog.getText(self, "Add Shader Override", "Enter shader resource name:")
        if not ok or not name.strip():
            return
        key = name.strip().lower()
        if key in (item.data(Qt.ItemDataRole.UserRole) for item in self._iter_list_items()):
            QMessageBox.information(self, "Shader Override", "Entry already exists.")
            return
        self._pending_overrides[key] = {"display_name": name.strip()}
        self.current_shader = key
        self.refresh_shader_list()

    def _iter_list_items(self):
        for idx in range(self.shader_list.count()):
            yield self.shader_list.item(idx)

    def get_overrides(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._pending_overrides)

    def reset_overrides(self):
        self._pending_overrides = dict(self.shader_registry.get_override_payloads())
        self.refresh_shader_list()

    # -------------------------------------------------------- default dirs/hints

    def _compute_shader_dir(self) -> Path:
        if self.game_path:
            candidate = self.game_path / "data" / "shaders"
            if candidate.exists():
                return candidate
        example = self.shader_registry.project_root / "My Singing Monsters Game Filesystem Example" / "data" / "shaders"
        if example.exists():
            return example
        return Path.home()

    def _compute_texture_dir(self) -> Path:
        if self.game_path:
            costume_dir = self.game_path / "data" / "gfx" / "costumes"
            if costume_dir.exists():
                return costume_dir
            gfx_dir = self.game_path / "data" / "gfx"
            if gfx_dir.exists():
                return gfx_dir
        example = self.shader_registry.project_root / "My Singing Monsters Game Filesystem Example" / "data" / "gfx" / "costumes"
        if example.exists():
            return example
        return Path.home()

    def _texture_hint_text(self) -> str:
        if self.texture_dir and self.texture_dir.exists():
            return f"Hint: Costume shader textures live in '{self.texture_dir}'."
        return "Hint: Costume shader textures are usually under data/gfx/costumes in the game files."


class SettingsDialog(QDialog):
    """Settings dialog with export options"""
    
    def __init__(
        self,
        export_settings: ExportSettings,
        app_settings: QSettings,
        shader_registry: ShaderRegistry,
        game_path: Optional[str],
        parent=None,
    ):
        super().__init__(parent)
        self.export_settings = export_settings
        self.app_settings = app_settings
        self.game_path = Path(game_path) if game_path else None
        self._game_path_str = game_path
        self.shader_registry = shader_registry
        self.setWindowTitle("Settings")
        self.setMinimumWidth(500)
        self.setMinimumHeight(450)
        self.ffmpeg_thread: QThread | None = None
        self.ffmpeg_worker: FFmpegInstallWorker | None = None
        self.ffmpeg_install_running = False
        
        self.init_ui()
        self.load_current_settings()
    
    def init_ui(self):
        """Initialize the UI"""
        layout = QVBoxLayout(self)
        
        # Tab widget for different settings categories
        self.tab_widget = QTabWidget()
        
        # PNG Settings
        png_group = QGroupBox("PNG Export Settings")
        png_layout = QFormLayout()
        
        self.png_compression_spin = QSpinBox()
        self.png_compression_spin.setRange(0, 9)
        self.png_compression_spin.setToolTip("0 = No compression (fastest, largest)\n9 = Maximum compression (slowest, smallest)")
        png_layout.addRow("Compression Level (0-9):", self.png_compression_spin)

        self.png_full_res_check = QCheckBox("Enable")
        self.png_full_res_check.setToolTip(
            "When enabled, PNG exports render using the raw sprite bounds so no details are lost."
        )
        self.png_full_res_check.stateChanged.connect(self._update_png_full_res_controls)
        png_layout.addRow("Full Resolution Output:", self.png_full_res_check)

        self.png_full_res_multiplier_spin = QDoubleSpinBox()
        self.png_full_res_multiplier_spin.setRange(1.0, 8.0)
        self.png_full_res_multiplier_spin.setSingleStep(0.25)
        self.png_full_res_multiplier_spin.setDecimals(2)
        self.png_full_res_multiplier_spin.setToolTip(
            "Additional scale multiplier applied when PNG full-resolution mode is enabled."
        )
        self.png_full_res_multiplier_spin.setEnabled(False)
        png_layout.addRow("Full Res Scale Multiplier:", self.png_full_res_multiplier_spin)
        
        png_info = QLabel("Higher compression = smaller file but slower export")
        png_info.setStyleSheet("color: gray; font-size: 9pt;")
        png_layout.addRow("", png_info)
        
        png_group.setLayout(png_layout)

        png_tab = QWidget()
        png_tab_layout = QVBoxLayout(png_tab)
        png_tab_layout.addWidget(png_group)
        png_tab_layout.addStretch()
        self.tab_widget.addTab(png_tab, "PNG")

        # GIF Settings
        gif_group = QGroupBox("GIF Export Settings")
        gif_layout = QFormLayout()
        
        self.gif_fps_spin = QSpinBox()
        self.gif_fps_spin.setRange(1, 60)
        self.gif_fps_spin.setToolTip("Frames per second for GIF animation (up to 60 FPS)")
        self.gif_fps_spin.valueChanged.connect(self.update_gif_estimate)
        gif_layout.addRow("FPS:", self.gif_fps_spin)
        
        self.gif_colors_combo = QComboBox()
        self.gif_colors_combo.addItems(['16', '32', '64', '128', '256'])
        self.gif_colors_combo.setToolTip("Number of colors in the GIF palette\nMore colors = better quality but larger file")
        self.gif_colors_combo.currentTextChanged.connect(self.update_gif_estimate)
        gif_layout.addRow("Colors:", self.gif_colors_combo)
        
        self.gif_scale_spin = QSpinBox()
        self.gif_scale_spin.setRange(10, 200)
        self.gif_scale_spin.setSuffix("%")
        self.gif_scale_spin.setToolTip("Scale the output GIF (100% = original size)")
        self.gif_scale_spin.valueChanged.connect(self.update_gif_estimate)
        gif_layout.addRow("Output Scale:", self.gif_scale_spin)
        
        self.gif_dither_check = QCheckBox()
        self.gif_dither_check.setToolTip("Apply dithering to reduce color banding")
        self.gif_dither_check.stateChanged.connect(self.update_gif_estimate)
        gif_layout.addRow("Dithering:", self.gif_dither_check)
        
        self.gif_optimize_check = QCheckBox()
        self.gif_optimize_check.setToolTip("Optimize GIF for smaller file size")
        self.gif_optimize_check.stateChanged.connect(self.update_gif_estimate)
        gif_layout.addRow("Optimize:", self.gif_optimize_check)
        
        self.gif_loop_spin = QSpinBox()
        self.gif_loop_spin.setRange(0, 100)
        self.gif_loop_spin.setSpecialValueText("Infinite")
        self.gif_loop_spin.setToolTip("0 = Loop forever, 1+ = specific number of loops")
        gif_layout.addRow("Loop Count:", self.gif_loop_spin)
        
        # GIF size estimate
        estimate_frame = QFrame()
        estimate_frame.setFrameStyle(QFrame.Shape.StyledPanel)
        estimate_layout = QVBoxLayout(estimate_frame)
        
        self.gif_estimate_label = QLabel("Estimated file size: ~0 KB")
        self.gif_estimate_label.setStyleSheet("font-weight: bold;")
        estimate_layout.addWidget(self.gif_estimate_label)
        
        self.gif_quality_label = QLabel("Quality: High")
        self.gif_quality_label.setStyleSheet("color: green;")
        estimate_layout.addWidget(self.gif_quality_label)
        
        gif_layout.addRow("", estimate_frame)
        
        gif_group.setLayout(gif_layout)

        gif_tab = QWidget()
        gif_tab_layout = QVBoxLayout(gif_tab)
        gif_tab_layout.addWidget(gif_group)
        gif_tab_layout.addStretch()
        self.tab_widget.addTab(gif_tab, "GIF")

        # MOV Settings
        mov_group = QGroupBox("MOV/Video Export Settings")
        mov_layout = QFormLayout()
        
        self.mov_codec_combo = QComboBox()
        self.mov_codec_combo.addItems([
            'prores_ks - ProRes 4444 (Best for Adobe)',
            'png - PNG Codec (Good Alpha)',
            'qtrle - QuickTime Animation (Legacy)',
            'libx264 - H.264 (No Alpha, Smallest)'
        ])
        self.mov_codec_combo.setToolTip("Video codec for MOV export\nProRes 4444 recommended for Adobe Premiere/After Effects")
        self.mov_codec_combo.currentTextChanged.connect(self.update_mov_estimate)
        mov_layout.addRow("Codec:", self.mov_codec_combo)
        
        self.mov_quality_combo = QComboBox()
        self.mov_quality_combo.addItems(['Low', 'Medium', 'High', 'Lossless'])
        self.mov_quality_combo.setCurrentText('High')
        self.mov_quality_combo.currentTextChanged.connect(self.update_mov_estimate)
        mov_layout.addRow("Quality:", self.mov_quality_combo)

        self.mov_include_audio_check = QCheckBox("Embed audio track if available")
        self.mov_include_audio_check.setToolTip("Include the loaded monster audio in the exported MOV when available")
        mov_layout.addRow("", self.mov_include_audio_check)

        full_res_row = QHBoxLayout()
        self.mov_full_res_check = QCheckBox("Enable")
        self.mov_full_res_check.setToolTip(
            "When enabled, MOV exports render at the raw sprite bounds for every frame, "
            "so no per-layer detail is lost."
        )
        self.mov_full_res_check.stateChanged.connect(self._update_mov_full_res_controls)
        full_res_row.addWidget(self.mov_full_res_check)

        self.mov_full_res_multiplier_spin = QDoubleSpinBox()
        self.mov_full_res_multiplier_spin.setRange(1.0, 8.0)
        self.mov_full_res_multiplier_spin.setSingleStep(0.25)
        self.mov_full_res_multiplier_spin.setDecimals(2)
        self.mov_full_res_multiplier_spin.setToolTip(
            "Additional scale multiplier applied when MOV full-resolution mode is enabled."
        )
        self.mov_full_res_multiplier_spin.setEnabled(False)
        full_res_row.addWidget(self.mov_full_res_multiplier_spin)

        mov_layout.addRow("Full Resolution Output:", full_res_row)
        
        # MOV size estimate
        mov_estimate_frame = QFrame()
        mov_estimate_frame.setFrameStyle(QFrame.Shape.StyledPanel)
        mov_estimate_layout = QVBoxLayout(mov_estimate_frame)
        
        self.mov_estimate_label = QLabel("Estimated file size: ~0 MB")
        self.mov_estimate_label.setStyleSheet("font-weight: bold;")
        mov_estimate_layout.addWidget(self.mov_estimate_label)
        
        self.mov_alpha_label = QLabel("Alpha: Supported")
        self.mov_alpha_label.setStyleSheet("color: green;")
        mov_estimate_layout.addWidget(self.mov_alpha_label)
        
        mov_layout.addRow("", mov_estimate_frame)
        
        mov_group.setLayout(mov_layout)

        mov_tab = QWidget()
        mov_tab_layout = QVBoxLayout(mov_tab)
        mov_tab_layout.addWidget(mov_group)
        mov_tab_layout.addStretch()
        self.tab_widget.addTab(mov_tab, "MOV")

        # WEBM Settings
        webm_group = QGroupBox("WEBM Export Settings")
        webm_layout = QFormLayout()

        self.webm_codec_combo = QComboBox()
        self.webm_codec_combo.addItems([
            'libvpx-vp9 - VP9 (Alpha, Discord Recommended)',
            'libaom-av1 - AV1 (Experimental, Alpha)',
            'libvpx - VP8 (Legacy, No Alpha)'
        ])
        self.webm_codec_combo.currentTextChanged.connect(self.update_webm_estimate)
        webm_layout.addRow("Codec:", self.webm_codec_combo)

        self.webm_crf_spin = QSpinBox()
        self.webm_crf_spin.setRange(0, 63)
        self.webm_crf_spin.setValue(28)
        self.webm_crf_spin.setToolTip("Quality/CRF (lower = better quality, higher = smaller files)")
        self.webm_crf_spin.valueChanged.connect(self.update_webm_estimate)
        webm_layout.addRow("Quality (CRF):", self.webm_crf_spin)

        self.webm_speed_spin = QSpinBox()
        self.webm_speed_spin.setRange(0, 8)
        self.webm_speed_spin.setValue(4)
        self.webm_speed_spin.setToolTip("Encoding speed (lower = slower encode but potentially smaller files)")
        webm_layout.addRow("Encoder Speed:", self.webm_speed_spin)

        self.webm_include_audio_check = QCheckBox("Embed audio track if available")
        webm_layout.addRow("", self.webm_include_audio_check)

        webm_full_res_row = QHBoxLayout()
        self.webm_full_res_check = QCheckBox("Enable")
        self.webm_full_res_check.setToolTip(
            "When enabled, WEBM exports render at the raw sprite bounds for every frame to preserve detail."
        )
        self.webm_full_res_check.stateChanged.connect(self._update_webm_full_res_controls)
        webm_full_res_row.addWidget(self.webm_full_res_check)

        self.webm_full_res_multiplier_spin = QDoubleSpinBox()
        self.webm_full_res_multiplier_spin.setRange(1.0, 8.0)
        self.webm_full_res_multiplier_spin.setSingleStep(0.25)
        self.webm_full_res_multiplier_spin.setDecimals(2)
        self.webm_full_res_multiplier_spin.setToolTip(
            "Additional scale multiplier applied when WEBM full-resolution mode is enabled."
        )
        self.webm_full_res_multiplier_spin.setEnabled(False)
        webm_full_res_row.addWidget(self.webm_full_res_multiplier_spin)

        webm_layout.addRow("Full Resolution Output:", webm_full_res_row)

        # WEBM size/alpha estimate
        webm_estimate_frame = QFrame()
        webm_estimate_frame.setFrameStyle(QFrame.Shape.StyledPanel)
        webm_estimate_layout = QVBoxLayout(webm_estimate_frame)

        self.webm_estimate_label = QLabel("Estimated file size: ~0 MB")
        self.webm_estimate_label.setStyleSheet("font-weight: bold;")
        webm_estimate_layout.addWidget(self.webm_estimate_label)

        self.webm_alpha_label = QLabel("Alpha: Supported")
        self.webm_alpha_label.setStyleSheet("color: green;")
        webm_estimate_layout.addWidget(self.webm_alpha_label)

        webm_layout.addRow("", webm_estimate_frame)
        
        webm_group.setLayout(webm_layout)

        webm_tab = QWidget()
        webm_tab_layout = QVBoxLayout(webm_tab)
        webm_tab_layout.addWidget(webm_group)
        webm_tab_layout.addStretch()
        self.tab_widget.addTab(webm_tab, "WEBM")

        # MP4 Settings
        mp4_group = QGroupBox("MP4 Export Settings")
        mp4_layout = QFormLayout()

        self.mp4_codec_combo = QComboBox()
        self.mp4_codec_combo.addItems([
            "libx264 - H.264 (Most compatible)",
            "libx265 - H.265 / HEVC (Smaller files, slower)",
        ])
        self.mp4_codec_combo.currentTextChanged.connect(self.update_mp4_estimate)
        mp4_layout.addRow("Codec:", self.mp4_codec_combo)

        self.mp4_crf_spin = QSpinBox()
        self.mp4_crf_spin.setRange(0, 51)
        self.mp4_crf_spin.setValue(18)
        self.mp4_crf_spin.setToolTip("Quality/CRF (lower = higher quality, higher = smaller files)")
        self.mp4_crf_spin.valueChanged.connect(self.update_mp4_estimate)
        mp4_layout.addRow("Quality (CRF):", self.mp4_crf_spin)

        self.mp4_preset_combo = QComboBox()
        self.mp4_preset_combo.addItems([
            "ultrafast", "superfast", "veryfast", "faster", "fast",
            "medium", "slow", "slower", "veryslow",
        ])
        self.mp4_preset_combo.setCurrentText("medium")
        self.mp4_preset_combo.currentTextChanged.connect(self.update_mp4_estimate)
        mp4_layout.addRow("Encoder Preset:", self.mp4_preset_combo)

        self.mp4_bitrate_spin = QSpinBox()
        self.mp4_bitrate_spin.setRange(0, 200000)
        self.mp4_bitrate_spin.setSingleStep(500)
        self.mp4_bitrate_spin.setToolTip("Optional video bitrate cap in kbps (0 = use CRF only)")
        self.mp4_bitrate_spin.valueChanged.connect(self.update_mp4_estimate)
        mp4_layout.addRow("Bitrate Cap (kbps):", self.mp4_bitrate_spin)

        self.mp4_include_audio_check = QCheckBox("Embed audio track if available")
        mp4_layout.addRow("", self.mp4_include_audio_check)

        pixel_fmt_row = QHBoxLayout()
        self.mp4_pixel_format_combo = QComboBox()
        self.mp4_pixel_format_combo.addItems([
            "yuv420p - Maximum compatibility",
            "yuv444p - 4:4:4 chroma (limited support)",
        ])
        self.mp4_pixel_format_combo.currentTextChanged.connect(self.update_mp4_estimate)
        pixel_fmt_row.addWidget(self.mp4_pixel_format_combo)
        mp4_layout.addRow("Pixel Format:", pixel_fmt_row)

        self.mp4_faststart_check = QCheckBox("Optimize for streaming (faststart)")
        self.mp4_faststart_check.setToolTip("Moves MP4 metadata to the start of the file for faster playback on the web.")
        mp4_layout.addRow("", self.mp4_faststart_check)

        mp4_full_res_row = QHBoxLayout()
        self.mp4_full_res_check = QCheckBox("Enable")
        self.mp4_full_res_check.setToolTip(
            "When enabled, MP4 exports render at the raw sprite bounds each frame to maximize detail."
        )
        self.mp4_full_res_check.stateChanged.connect(self._update_mp4_full_res_controls)
        mp4_full_res_row.addWidget(self.mp4_full_res_check)

        self.mp4_full_res_multiplier_spin = QDoubleSpinBox()
        self.mp4_full_res_multiplier_spin.setRange(1.0, 8.0)
        self.mp4_full_res_multiplier_spin.setSingleStep(0.25)
        self.mp4_full_res_multiplier_spin.setDecimals(2)
        self.mp4_full_res_multiplier_spin.setToolTip("Additional scale multiplier when MP4 full-resolution mode is enabled.")
        self.mp4_full_res_multiplier_spin.setEnabled(False)
        mp4_full_res_row.addWidget(self.mp4_full_res_multiplier_spin)
        mp4_layout.addRow("Full Resolution Output:", mp4_full_res_row)

        mp4_estimate_frame = QFrame()
        mp4_estimate_frame.setFrameStyle(QFrame.Shape.StyledPanel)
        mp4_estimate_layout = QVBoxLayout(mp4_estimate_frame)

        self.mp4_estimate_label = QLabel("Estimated file size: ~0 MB")
        self.mp4_estimate_label.setStyleSheet("font-weight: bold;")
        mp4_estimate_layout.addWidget(self.mp4_estimate_label)

        self.mp4_alpha_label = QLabel("Alpha: Not Supported (opaque output)")
        self.mp4_alpha_label.setStyleSheet("color: red;")
        mp4_estimate_layout.addWidget(self.mp4_alpha_label)

        mp4_layout.addRow("", mp4_estimate_frame)

        mp4_group.setLayout(mp4_layout)

        mp4_tab = QWidget()
        mp4_tab_layout = QVBoxLayout(mp4_tab)
        mp4_tab_layout.addWidget(mp4_group)
        mp4_tab_layout.addStretch()
        self.tab_widget.addTab(mp4_tab, "MP4")

        # PSD Settings
        psd_group = QGroupBox("PSD Export Settings")
        psd_layout = QFormLayout()
        
        self.psd_hidden_check = QCheckBox()
        self.psd_hidden_check.setToolTip("Include hidden layers in PSD export")
        psd_layout.addRow("Include Hidden Layers:", self.psd_hidden_check)

        self.psd_full_res_check = QCheckBox("Preserve sprite-native resolution")
        self.psd_full_res_check.setToolTip(
            "Ignores the PSD scale and viewer zoom so each sprite layer is exported at its native resolution."
        )
        self.psd_full_res_check.stateChanged.connect(self._update_psd_full_res_controls)
        psd_layout.addRow("Full Resolution Output:", self.psd_full_res_check)

        self.psd_full_res_multiplier_spin = QDoubleSpinBox()
        self.psd_full_res_multiplier_spin.setRange(1.0, 8.0)
        self.psd_full_res_multiplier_spin.setSingleStep(0.25)
        self.psd_full_res_multiplier_spin.setDecimals(2)
        self.psd_full_res_multiplier_spin.setToolTip(
            "Additional scale multiplier to apply on top of the native sprite resolution "
            "when 'Full Resolution Output' is enabled."
        )
        self.psd_full_res_multiplier_spin.setEnabled(False)
        psd_layout.addRow("Full Res Scale Multiplier:", self.psd_full_res_multiplier_spin)
        
        self.psd_quality_combo = QComboBox()
        self.psd_quality_combo.addItem("Fast (Nearest)", "fast")
        self.psd_quality_combo.addItem("Balanced (Bilinear)", "balanced")
        self.psd_quality_combo.addItem("High (Bicubic)", "high")
        self.psd_quality_combo.addItem("Maximum (Lanczos)", "maximum")
        self.psd_quality_combo.setToolTip("Higher quality uses more advanced filtering when transforming sprites")
        psd_layout.addRow("Layer Quality:", self.psd_quality_combo)
        
        self.psd_scale_spin = QSpinBox()
        self.psd_scale_spin.setRange(25, 400)
        self.psd_scale_spin.setSuffix("%")
        self.psd_scale_spin.setToolTip("Scale exported PSD relative to the current viewport size")
        psd_layout.addRow("Resolution Scale:", self.psd_scale_spin)
        
        self.psd_compression_combo = QComboBox()
        self.psd_compression_combo.addItem("Uncompressed (Largest, Fastest)", "raw")
        self.psd_compression_combo.addItem("RLE Compression (Smaller, Slower)", "rle")
        self.psd_compression_combo.setToolTip("RLE compression creates smaller PSDs at the cost of a slightly slower export")
        psd_layout.addRow("Channel Compression:", self.psd_compression_combo)
        
        self.psd_crop_check = QCheckBox()
        self.psd_crop_check.setToolTip("Trim extra transparent pixels so the PSD canvas only covers visible content")
        psd_layout.addRow("Crop Canvas to Content:", self.psd_crop_check)
        
        self.psd_match_viewport_check = QCheckBox()
        self.psd_match_viewport_check.setToolTip("When enabled, PSD exports follow the viewer zoom/pan.\nWhen disabled, layers export at their full sprite resolution.")
        psd_layout.addRow("Match Viewer Zoom/Pan:", self.psd_match_viewport_check)
        
        psd_info = QLabel("Tweaking quality and compression affects export time and PSD size.")
        psd_info.setStyleSheet("color: gray; font-size: 9pt;")
        psd_layout.addRow("", psd_info)
        
        psd_group.setLayout(psd_layout)

        psd_tab = QWidget()
        psd_tab_layout = QVBoxLayout(psd_tab)
        psd_tab_layout.addWidget(psd_group)
        psd_tab_layout.addStretch()
        self.tab_widget.addTab(psd_tab, "PSD")

        # Shader settings tab
        shader_game_path = str(self.game_path) if self.game_path else self._game_path_str
        self.shader_tab = ShaderSettingsWidget(
            self.shader_registry,
            shader_game_path,
        )
        self.tab_widget.addTab(self.shader_tab, "Shaders")

        # Application settings tab
        app_tab = QWidget()
        app_layout = QVBoxLayout(app_tab)

        camera_group = QGroupBox("Camera & View Settings")
        camera_form = QFormLayout()

        self.camera_zoom_cursor_check = QCheckBox("Zoom towards mouse cursor")
        self.camera_zoom_cursor_check.setToolTip("When enabled, scroll zooms around the cursor position instead of the animation center")
        camera_form.addRow(self.camera_zoom_cursor_check)

        camera_group.setLayout(camera_form)
        app_layout.addWidget(camera_group)

        file_browser_group = QGroupBox("File Browsing")
        file_browser_layout = QVBoxLayout()
        self.barebones_browser_check = QCheckBox("Barebones search for BIN/JSON files")
        self.barebones_browser_check.setToolTip(
            "Enable to keep the classic text list. Disable to use the Monster Browser grid with portraits."
        )
        file_browser_layout.addWidget(self.barebones_browser_check)
        browser_hint = QLabel("Monster Browser loads portraits from data/gfx/book and supports auto conversion.")
        browser_hint.setWordWrap(True)
        browser_hint.setStyleSheet("color: gray; font-size: 9pt;")
        file_browser_layout.addWidget(browser_hint)
        file_browser_group.setLayout(file_browser_layout)
        app_layout.addWidget(file_browser_group)

        bin_group = QGroupBox("BIN Export")
        bin_layout = QVBoxLayout()
        self.update_source_json_check = QCheckBox("Update original JSON when saving animations")
        self.update_source_json_check.setToolTip(
            "When enabled, saving an animation also overwrites the currently loaded JSON file "
            "with the merged result."
        )
        bin_layout.addWidget(self.update_source_json_check)
        bin_hint = QLabel("Keeps the source JSON in sync without running a separate export.")
        bin_hint.setWordWrap(True)
        bin_hint.setStyleSheet("color: gray; font-size: 9pt;")
        bin_layout.addWidget(bin_hint)
        bin_group.setLayout(bin_layout)
        app_layout.addWidget(bin_group)

        ffmpeg_group = QGroupBox("FFmpeg Tools")
        ffmpeg_layout = QVBoxLayout()

        self.ffmpeg_status_label = QLabel()
        self.ffmpeg_status_label.setWordWrap(True)
        ffmpeg_layout.addWidget(self.ffmpeg_status_label)

        self.ffmpeg_progress = QProgressBar()
        self.ffmpeg_progress.setRange(0, 100)
        self.ffmpeg_progress.setVisible(False)
        ffmpeg_layout.addWidget(self.ffmpeg_progress)

        self.ffmpeg_install_button = QPushButton("Install FFmpeg")
        self.ffmpeg_install_button.clicked.connect(self.start_ffmpeg_install)
        ffmpeg_layout.addWidget(self.ffmpeg_install_button)

        ffmpeg_hint = QLabel(
            "MOV exports rely on FFmpeg. Click the button above for a "
            "one-click install that downloads and wires everything up."
        )
        ffmpeg_hint.setStyleSheet("color: gray; font-size: 9pt;")
        ffmpeg_hint.setWordWrap(True)
        ffmpeg_layout.addWidget(ffmpeg_hint)

        install_dir_label = QLabel(f"Install location: {get_install_root()}")
        install_dir_label.setStyleSheet("color: gray; font-size: 8pt;")
        install_dir_label.setWordWrap(True)
        ffmpeg_layout.addWidget(install_dir_label)

        ffmpeg_group.setLayout(ffmpeg_layout)
        app_layout.addWidget(ffmpeg_group)

        diag_group = self._build_diagnostics_group()
        app_layout.addWidget(diag_group)

        app_layout.addStretch()
        
        self.tab_widget.addTab(app_tab, "Application")
        
        layout.addWidget(self.tab_widget)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.reset_btn = QPushButton("Reset to Defaults")
        self.reset_btn.clicked.connect(self.reset_to_defaults)
        button_layout.addWidget(self.reset_btn)
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)
        
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self.save_settings)
        self.save_btn.setDefault(True)
        button_layout.addWidget(self.save_btn)
        
        layout.addLayout(button_layout)
    
    def _build_diagnostics_group(self) -> QGroupBox:
        group = QGroupBox("Diagnostics & Logging")
        layout = QFormLayout()

        self.diag_enable_check = QCheckBox("Enable diagnostics logging")
        self.diag_enable_check.toggled.connect(self._update_diag_controls)
        layout.addRow(self.diag_enable_check)

        self.diag_highlight_check = QCheckBox("Highlight problem layers in the list")
        layout.addRow(self.diag_highlight_check)

        self.diag_throttle_check = QCheckBox("Throttle layer status updates")
        layout.addRow(self.diag_throttle_check)

        self.diag_clone_check = QCheckBox("Log costume clone operations")
        layout.addRow(self.diag_clone_check)

        self.diag_canonical_check = QCheckBox("Log canonical/base clone seeding")
        layout.addRow(self.diag_canonical_check)

        self.diag_remap_check = QCheckBox("Log remap/swaps")
        layout.addRow(self.diag_remap_check)

        self.diag_sheet_check = QCheckBox("Log sheet alias activity")
        layout.addRow(self.diag_sheet_check)

        self.anchor_debug_check = QCheckBox("Enable anchor_debug.txt exports")
        self.anchor_debug_check.setToolTip(
            "When enabled, the viewer records detailed anchor metadata to anchor_debug.txt after loads. "
            "Disable if the extra logging causes slowdowns."
        )
        layout.addRow(self.anchor_debug_check)

        self.diag_visibility_check = QCheckBox("Log visibility toggles")
        layout.addRow(self.diag_visibility_check)

        self.diag_shader_check = QCheckBox("Log shader overrides")
        layout.addRow(self.diag_shader_check)

        self.diag_color_check = QCheckBox("Log tint/layer color overrides")
        layout.addRow(self.diag_color_check)

        self.diag_attachment_check = QCheckBox("Log attachment loading")
        layout.addRow(self.diag_attachment_check)

        self.diag_debug_payload_check = QCheckBox("Include debug payloads in log file")
        layout.addRow(self.diag_debug_payload_check)

        self.diag_min_severity_combo = QComboBox()
        self.diag_min_severity_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        layout.addRow("Minimum Severity:", self.diag_min_severity_combo)

        self.diag_update_interval_spin = QSpinBox()
        self.diag_update_interval_spin.setRange(50, 5000)
        self.diag_update_interval_spin.setSuffix(" ms")
        layout.addRow("Layer Status Refresh Interval:", self.diag_update_interval_spin)

        self.diag_status_duration_spin = QDoubleSpinBox()
        self.diag_status_duration_spin.setRange(1.0, 60.0)
        self.diag_status_duration_spin.setDecimals(1)
        self.diag_status_duration_spin.setSuffix(" sec")
        layout.addRow("Layer Status Lifetime:", self.diag_status_duration_spin)

        self.diag_rate_limit_spin = QSpinBox()
        self.diag_rate_limit_spin.setRange(10, 1000)
        layout.addRow("Max Events Per Second:", self.diag_rate_limit_spin)

        self.diag_max_entries_spin = QSpinBox()
        self.diag_max_entries_spin.setRange(100, 10000)
        layout.addRow("Log History Size:", self.diag_max_entries_spin)

        self.diag_auto_export_check = QCheckBox("Automatically export diagnostics log")
        self.diag_auto_export_check.toggled.connect(self._update_diag_export_controls)
        layout.addRow(self.diag_auto_export_check)

        self.diag_auto_export_interval_spin = QSpinBox()
        self.diag_auto_export_interval_spin.setRange(5, 3600)
        self.diag_auto_export_interval_spin.setSuffix(" sec")
        layout.addRow("Auto-Export Interval:", self.diag_auto_export_interval_spin)

        path_row = QHBoxLayout()
        self.diag_export_path_edit = QLineEdit()
        path_row.addWidget(self.diag_export_path_edit)
        self.diag_export_browse_btn = QPushButton("Browse…")
        self.diag_export_browse_btn.clicked.connect(self._browse_diag_export_path)
        path_row.addWidget(self.diag_export_browse_btn)
        layout.addRow("Export Location:", path_row)

        group.setLayout(layout)
        return group
    
    def load_current_settings(self):
        """Load current settings into UI"""
        # PNG
        self.png_compression_spin.setValue(self.export_settings.png_compression)
        self.png_full_res_check.setChecked(self.export_settings.png_full_resolution)
        self.png_full_res_multiplier_spin.setValue(
            max(1.0, float(getattr(self.export_settings, 'png_full_scale_multiplier', 1.0)))
        )
        self._update_png_full_res_controls()
        
        # GIF
        self.gif_fps_spin.setValue(self.export_settings.gif_fps)
        
        colors_index = ['16', '32', '64', '128', '256'].index(str(self.export_settings.gif_colors))
        self.gif_colors_combo.setCurrentIndex(colors_index)
        
        self.gif_scale_spin.setValue(self.export_settings.gif_scale)
        self.gif_dither_check.setChecked(self.export_settings.gif_dither)
        self.gif_optimize_check.setChecked(self.export_settings.gif_optimize)
        self.gif_loop_spin.setValue(self.export_settings.gif_loop)
        
        # MOV - order matches combo box: prores_ks, png, qtrle, libx264
        codec_map = {
            'prores_ks': 0,
            'png': 1,
            'qtrle': 2,
            'libx264': 3
        }
        self.mov_codec_combo.setCurrentIndex(codec_map.get(self.export_settings.mov_codec, 0))
        self.mov_quality_combo.setCurrentText(self.export_settings.mov_quality.capitalize())
        self.mov_include_audio_check.setChecked(self.export_settings.mov_include_audio)
        self.mov_full_res_check.setChecked(self.export_settings.mov_full_resolution)
        self.mov_full_res_multiplier_spin.setValue(
            max(1.0, float(getattr(self.export_settings, 'mov_full_scale_multiplier', 1.0)))
        )
        self._update_mov_full_res_controls()

        # WEBM
        webm_codec_text = getattr(self.export_settings, 'webm_codec', 'libvpx-vp9')
        for idx in range(self.webm_codec_combo.count()):
            if self.webm_codec_combo.itemText(idx).startswith(webm_codec_text):
                self.webm_codec_combo.setCurrentIndex(idx)
                break
        self.webm_crf_spin.setValue(int(getattr(self.export_settings, 'webm_crf', 28)))
        self.webm_speed_spin.setValue(int(getattr(self.export_settings, 'webm_speed', 4)))
        self.webm_include_audio_check.setChecked(getattr(self.export_settings, 'webm_include_audio', True))
        self.webm_full_res_check.setChecked(getattr(self.export_settings, 'webm_full_resolution', False))
        self.webm_full_res_multiplier_spin.setValue(
            max(1.0, float(getattr(self.export_settings, 'webm_full_scale_multiplier', 1.0)))
        )
        self._update_webm_full_res_controls()

        # MP4
        mp4_codec = getattr(self.export_settings, 'mp4_codec', 'libx264')
        for idx in range(self.mp4_codec_combo.count()):
            if self.mp4_codec_combo.itemText(idx).startswith(mp4_codec):
                self.mp4_codec_combo.setCurrentIndex(idx)
                break
        self.mp4_crf_spin.setValue(int(getattr(self.export_settings, 'mp4_crf', 18)))
        preset_value = getattr(self.export_settings, 'mp4_preset', 'medium').lower()
        preset_index = self.mp4_preset_combo.findText(preset_value, Qt.MatchFlag.MatchFixedString | Qt.MatchFlag.MatchCaseSensitive)
        if preset_index < 0:
            preset_index = self.mp4_preset_combo.findText(preset_value.capitalize())
        if preset_index < 0:
            preset_index = self.mp4_preset_combo.findText("medium", Qt.MatchFlag.MatchFixedString)
        if preset_index >= 0:
            self.mp4_preset_combo.setCurrentIndex(preset_index)
        self.mp4_bitrate_spin.setValue(int(getattr(self.export_settings, 'mp4_bitrate', 0)))
        self.mp4_include_audio_check.setChecked(getattr(self.export_settings, 'mp4_include_audio', True))
        self.mp4_full_res_check.setChecked(getattr(self.export_settings, 'mp4_full_resolution', False))
        self.mp4_full_res_multiplier_spin.setValue(
            max(1.0, float(getattr(self.export_settings, 'mp4_full_scale_multiplier', 1.0)))
        )
        pixel_fmt_value = getattr(self.export_settings, 'mp4_pixel_format', 'yuv420p')
        for idx in range(self.mp4_pixel_format_combo.count()):
            if self.mp4_pixel_format_combo.itemText(idx).startswith(pixel_fmt_value):
                self.mp4_pixel_format_combo.setCurrentIndex(idx)
                break
        self.mp4_faststart_check.setChecked(getattr(self.export_settings, 'mp4_faststart', True))
        self._update_mp4_full_res_controls()
        self.update_mp4_estimate()

        # Camera
        self.camera_zoom_cursor_check.setChecked(self.export_settings.camera_zoom_to_cursor)
        self.barebones_browser_check.setChecked(self.export_settings.use_barebones_file_browser)
        self.anchor_debug_check.setChecked(self.export_settings.anchor_debug_logging)
        self.update_source_json_check.setChecked(getattr(self.export_settings, 'update_source_json_on_save', False))
        
        # PSD
        self.psd_hidden_check.setChecked(self.export_settings.psd_include_hidden)
        self.psd_full_res_check.setChecked(self.export_settings.psd_preserve_resolution)
        self.psd_full_res_multiplier_spin.setValue(
            max(1.0, float(getattr(self.export_settings, 'psd_full_res_multiplier', 1.0)))
        )
        self.psd_scale_spin.setValue(self.export_settings.psd_scale)
        
        quality_index = self.psd_quality_combo.findData(self.export_settings.psd_quality)
        if quality_index == -1:
            quality_index = 1  # Balanced default
        self.psd_quality_combo.setCurrentIndex(quality_index)
        
        compression_index = self.psd_compression_combo.findData(self.export_settings.psd_compression)
        if compression_index == -1:
            compression_index = 1  # RLE default
        self.psd_compression_combo.setCurrentIndex(compression_index)
        
        self.psd_crop_check.setChecked(self.export_settings.psd_crop_canvas)
        self.psd_match_viewport_check.setChecked(self.export_settings.psd_match_viewport)

        # Update estimates
        self._load_diagnostics_settings()
        self.update_gif_estimate()
        self.update_mov_estimate()
        self.update_webm_estimate()
        self.update_mp4_estimate()
        self.update_ffmpeg_status()
        self._update_psd_full_res_controls()
        self._update_psd_full_res_controls()
        self._update_psd_full_res_controls()

    def _load_diagnostics_settings(self):
        s = self.app_settings
        get_bool = lambda key, default: s.value(f"diagnostics/{key}", default, type=bool)
        get_int = lambda key, default: s.value(f"diagnostics/{key}", default, type=int)
        get_float = lambda key, default: s.value(f"diagnostics/{key}", default, type=float)
        get_str = lambda key, default: s.value(f"diagnostics/{key}", default, type=str)

        self.diag_enable_check.setChecked(get_bool("enabled", False))
        self.diag_highlight_check.setChecked(get_bool("highlight_layers", True))
        self.diag_throttle_check.setChecked(get_bool("throttle_updates", True))
        self.diag_clone_check.setChecked(get_bool("log_clone_events", True))
        self.diag_canonical_check.setChecked(get_bool("log_canonical_events", True))
        self.diag_remap_check.setChecked(get_bool("log_remap_events", False))
        self.diag_sheet_check.setChecked(get_bool("log_sheet_events", False))
        self.diag_visibility_check.setChecked(get_bool("log_visibility_events", False))
        self.diag_shader_check.setChecked(get_bool("log_shader_events", False))
        self.diag_color_check.setChecked(get_bool("log_color_events", False))
        self.diag_attachment_check.setChecked(get_bool("log_attachment_events", False))
        self.diag_debug_payload_check.setChecked(get_bool("include_debug_payloads", False))
        severity = get_str("minimum_severity", "INFO")
        idx = self.diag_min_severity_combo.findText(severity)
        if idx == -1:
            idx = 1
        self.diag_min_severity_combo.setCurrentIndex(idx)
        self.diag_update_interval_spin.setValue(get_int("update_interval_ms", 500))
        self.diag_status_duration_spin.setValue(get_float("layer_status_duration_sec", 6.0))
        self.diag_rate_limit_spin.setValue(get_int("rate_limit_per_sec", 120))
        self.diag_max_entries_spin.setValue(get_int("max_entries", 2000))
        self.diag_auto_export_check.setChecked(get_bool("auto_export_enabled", False))
        self.diag_auto_export_interval_spin.setValue(get_int("auto_export_interval_sec", 120))
        self.diag_export_path_edit.setText(get_str("export_path", ""))
        self._update_diag_controls()
        self._update_diag_export_controls()

    def _update_diag_controls(self):
        enabled = self.diag_enable_check.isChecked()
        for widget in [
            self.diag_highlight_check,
            self.diag_throttle_check,
            self.diag_clone_check,
            self.diag_canonical_check,
            self.diag_remap_check,
            self.diag_sheet_check,
            self.diag_visibility_check,
            self.diag_shader_check,
            self.diag_color_check,
            self.diag_attachment_check,
            self.diag_debug_payload_check,
            self.diag_min_severity_combo,
            self.diag_update_interval_spin,
            self.diag_status_duration_spin,
            self.diag_rate_limit_spin,
            self.diag_max_entries_spin,
            self.diag_auto_export_check,
            self.diag_auto_export_interval_spin,
            self.diag_export_path_edit,
        ]:
            widget.setEnabled(enabled)
        self._update_diag_export_controls()

    def _update_diag_export_controls(self):
        enabled = (
            self.diag_enable_check.isChecked() and
            self.diag_auto_export_check.isChecked()
        )
        self.diag_auto_export_interval_spin.setEnabled(enabled)
        self.diag_export_path_edit.setEnabled(enabled)
        self.diag_export_browse_btn.setEnabled(enabled)

    def _browse_diag_export_path(self):
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Choose Diagnostics Log Destination",
            self.diag_export_path_edit.text() or str(Path.home() / "diagnostics.log"),
            "Log Files (*.log *.txt);;All Files (*)"
        )
        if filename:
            self.diag_export_path_edit.setText(filename)

    def _save_diagnostics_settings(self):
        s = self.app_settings
        s.setValue("diagnostics/enabled", self.diag_enable_check.isChecked())
        s.setValue("diagnostics/highlight_layers", self.diag_highlight_check.isChecked())
        s.setValue("diagnostics/throttle_updates", self.diag_throttle_check.isChecked())
        s.setValue("diagnostics/log_clone_events", self.diag_clone_check.isChecked())
        s.setValue("diagnostics/log_canonical_events", self.diag_canonical_check.isChecked())
        s.setValue("diagnostics/log_remap_events", self.diag_remap_check.isChecked())
        s.setValue("diagnostics/log_sheet_events", self.diag_sheet_check.isChecked())
        s.setValue("diagnostics/log_visibility_events", self.diag_visibility_check.isChecked())
        s.setValue("diagnostics/log_shader_events", self.diag_shader_check.isChecked())
        s.setValue("diagnostics/log_color_events", self.diag_color_check.isChecked())
        s.setValue("diagnostics/log_attachment_events", self.diag_attachment_check.isChecked())
        s.setValue("diagnostics/include_debug_payloads", self.diag_debug_payload_check.isChecked())
        s.setValue("diagnostics/minimum_severity", self.diag_min_severity_combo.currentText())
        s.setValue("diagnostics/update_interval_ms", self.diag_update_interval_spin.value())
        s.setValue("diagnostics/layer_status_duration_sec", self.diag_status_duration_spin.value())
        s.setValue("diagnostics/rate_limit_per_sec", self.diag_rate_limit_spin.value())
        s.setValue("diagnostics/max_entries", self.diag_max_entries_spin.value())
        s.setValue("diagnostics/auto_export_enabled", self.diag_auto_export_check.isChecked())
        s.setValue("diagnostics/auto_export_interval_sec", self.diag_auto_export_interval_spin.value())
        s.setValue("diagnostics/export_path", self.diag_export_path_edit.text().strip())
        self._load_diagnostics_settings()
    
    def update_gif_estimate(self):
        """Update GIF file size estimate"""
        fps = self.gif_fps_spin.value()
        colors = int(self.gif_colors_combo.currentText())
        scale = self.gif_scale_spin.value() / 100.0
        dither = self.gif_dither_check.isChecked()
        optimize = self.gif_optimize_check.isChecked()
        
        # Base estimate: assume 500x500 animation, 3 seconds
        # This is a rough estimate - actual size depends on content
        base_frame_size = 500 * 500 * scale * scale  # pixels
        
        # Color depth factor (fewer colors = smaller)
        color_factor = colors / 256.0
        
        # Frames
        frames = fps * 3  # Assume 3 second animation
        
        # Base size in KB
        base_size = (base_frame_size * color_factor * frames) / 1024 / 8
        
        # Optimization factor
        if optimize:
            base_size *= 0.7
        
        # Dithering adds some overhead
        if dither:
            base_size *= 1.1
        
        # Clamp to reasonable range
        estimated_kb = max(50, min(base_size, 50000))
        
        if estimated_kb > 1024:
            self.gif_estimate_label.setText(f"Estimated file size: ~{estimated_kb/1024:.1f} MB")
        else:
            self.gif_estimate_label.setText(f"Estimated file size: ~{estimated_kb:.0f} KB")
        
        # Quality indicator
        if colors >= 256 and scale >= 100:
            quality_text = "Quality: High"
            quality_style = "color: green;"
        elif colors >= 128 and scale >= 75:
            quality_text = "Quality: Medium"
            quality_style = "color: orange;"
        else:
            quality_text = "Quality: Low"
            quality_style = "color: red;"
        
        # Add FPS warning for high frame rates
        if fps > 50:
            quality_text += " (Note: Some viewers may not display >50 FPS correctly)"
            quality_style = "color: orange;"
        
        self.gif_quality_label.setText(quality_text)
        self.gif_quality_label.setStyleSheet(quality_style)
    
    def update_mov_estimate(self):
        """Update MOV file size estimate"""
        codec_text = self.mov_codec_combo.currentText()
        quality = self.mov_quality_combo.currentText().lower()
        
        # Extract codec name
        codec = codec_text.split(' - ')[0] if ' - ' in codec_text else codec_text
        
        # Base estimate for 500x500, 3 second, 30fps video
        base_mb = 5.0
        
        # Codec factors
        codec_factors = {
            'qtrle': 3.0,      # Large but lossless with alpha
            'png': 2.5,        # Large with alpha
            'prores_ks': 4.0,  # Very large, professional
            'libx264': 0.5     # Small, no alpha
        }
        
        # Quality factors
        quality_factors = {
            'low': 0.5,
            'medium': 0.75,
            'high': 1.0,
            'lossless': 2.0
        }
        
        estimated_mb = base_mb * codec_factors.get(codec, 1.0) * quality_factors.get(quality, 1.0)
        
        self.mov_estimate_label.setText(f"Estimated file size: ~{estimated_mb:.1f} MB")
        
        # Alpha support indicator
        if codec in ['qtrle', 'png', 'prores_ks']:
            self.mov_alpha_label.setText("Alpha: Supported (transparency preserved)")
            self.mov_alpha_label.setStyleSheet("color: green;")
        else:
            self.mov_alpha_label.setText("Alpha: Not Supported (opaque output)")
            self.mov_alpha_label.setStyleSheet("color: red;")

    def update_webm_estimate(self):
        """Update WEBM file size and alpha estimate."""
        codec_text = self.webm_codec_combo.currentText()
        codec = codec_text.split(' - ')[0] if ' - ' in codec_text else codec_text
        crf = self.webm_crf_spin.value()

        # Rough estimate values
        base_mb = 3.0
        codec_factors = {
            'libvpx-vp9': 0.8,
            'libaom-av1': 0.6,
            'libvpx': 1.0,
        }
        quality_factor = max(0.2, (40 - crf) / 40.0)
        estimated_mb = base_mb * codec_factors.get(codec, 1.0) * quality_factor
        self.webm_estimate_label.setText(f"Estimated file size: ~{estimated_mb:.1f} MB")

        if codec in ('libvpx-vp9', 'libaom-av1'):
            self.webm_alpha_label.setText("Alpha: Supported (transparency preserved)")
            self.webm_alpha_label.setStyleSheet("color: green;")
        else:
            self.webm_alpha_label.setText("Alpha: Not Supported (opaque output)")
            self.webm_alpha_label.setStyleSheet("color: red;")

    def update_mp4_estimate(self):
        """Estimate MP4 file size / quality."""
        codec_text = self.mp4_codec_combo.currentText()
        codec = codec_text.split(' - ')[0] if ' - ' in codec_text else codec_text
        crf = self.mp4_crf_spin.value()
        bitrate = self.mp4_bitrate_spin.value()
        preset = self.mp4_preset_combo.currentText().lower()

        base_mb = 4.0
        codec_factors = {
            'libx264': 1.0,
            'libx265': 0.7,
        }
        preset_factors = {
            'ultrafast': 1.4,
            'superfast': 1.3,
            'veryfast': 1.2,
            'faster': 1.1,
            'fast': 1.0,
            'medium': 1.0,
            'slow': 0.9,
            'slower': 0.85,
            'veryslow': 0.8,
        }
        quality_factor = max(0.3, (40 - crf) / 40.0)
        estimated_mb = base_mb * codec_factors.get(codec, 1.0) * preset_factors.get(preset, 1.0) * quality_factor
        if bitrate > 0:
            estimated_mb = max(estimated_mb, bitrate / 8000.0 * 3.0)

        self.mp4_estimate_label.setText(f"Estimated file size: ~{estimated_mb:.1f} MB")
        self.mp4_alpha_label.setText("Alpha: Not Supported (opaque output)")
        self.mp4_alpha_label.setStyleSheet("color: red;")
    
    def reset_to_defaults(self):
        """Reset all settings to defaults"""
        # PNG
        self.png_compression_spin.setValue(6)
        self.png_full_res_check.setChecked(False)
        self.png_full_res_multiplier_spin.setValue(1.0)
        self._update_png_full_res_controls()
        
        # GIF
        self.gif_fps_spin.setValue(15)
        self.gif_colors_combo.setCurrentText('256')
        self.gif_scale_spin.setValue(100)
        self.gif_dither_check.setChecked(True)
        self.gif_optimize_check.setChecked(True)
        self.gif_loop_spin.setValue(0)
        
        # MOV
        self.mov_codec_combo.setCurrentIndex(0)
        self.mov_quality_combo.setCurrentText('High')
        self.mov_include_audio_check.setChecked(True)
        self.mov_full_res_check.setChecked(False)
        self.mov_full_res_multiplier_spin.setValue(1.0)
        self._update_mov_full_res_controls()

        # WEBM
        self.webm_codec_combo.setCurrentIndex(0)
        self.webm_crf_spin.setValue(28)
        self.webm_speed_spin.setValue(4)
        self.webm_include_audio_check.setChecked(True)
        self.webm_full_res_check.setChecked(False)
        self.webm_full_res_multiplier_spin.setValue(1.0)
        self._update_webm_full_res_controls()

        # MP4
        self.mp4_codec_combo.setCurrentIndex(0)
        self.mp4_crf_spin.setValue(18)
        self.mp4_preset_combo.setCurrentText("medium")
        self.mp4_bitrate_spin.setValue(0)
        self.mp4_include_audio_check.setChecked(True)
        self.mp4_full_res_check.setChecked(False)
        self.mp4_full_res_multiplier_spin.setValue(1.0)
        self.mp4_pixel_format_combo.setCurrentIndex(0)
        self.mp4_faststart_check.setChecked(True)
        self._update_mp4_full_res_controls()

        self.camera_zoom_cursor_check.setChecked(True)
        self.barebones_browser_check.setChecked(False)
        self.update_source_json_check.setChecked(False)
        
        # PSD
        self.psd_hidden_check.setChecked(False)
        self.psd_full_res_check.setChecked(False)
        self.psd_full_res_multiplier_spin.setValue(1.0)
        self.psd_scale_spin.setValue(100)
        self.psd_quality_combo.setCurrentIndex(1)
        self.psd_compression_combo.setCurrentIndex(1)
        self.psd_crop_check.setChecked(False)
        self.psd_match_viewport_check.setChecked(False)

        self.diag_enable_check.setChecked(False)
        self.diag_highlight_check.setChecked(True)
        self.diag_throttle_check.setChecked(True)
        self.diag_clone_check.setChecked(True)
        self.diag_canonical_check.setChecked(True)
        self.diag_remap_check.setChecked(False)
        self.diag_sheet_check.setChecked(False)
        self.diag_visibility_check.setChecked(False)
        self.diag_shader_check.setChecked(False)
        self.diag_color_check.setChecked(False)
        self.diag_attachment_check.setChecked(False)
        self.diag_debug_payload_check.setChecked(False)
        self.diag_min_severity_combo.setCurrentText("INFO")
        self.diag_update_interval_spin.setValue(500)
        self.diag_status_duration_spin.setValue(6.0)
        self.diag_rate_limit_spin.setValue(120)
        self.diag_max_entries_spin.setValue(2000)
        self.diag_auto_export_check.setChecked(False)
        self.diag_auto_export_interval_spin.setValue(120)
        self.diag_export_path_edit.setText("")
        self._update_diag_controls()
        self.anchor_debug_check.setChecked(False)

        self.update_gif_estimate()
        self.update_mov_estimate()
        self.update_webm_estimate()
        self.update_ffmpeg_status()
        self.shader_tab.reset_overrides()
    
    def save_settings(self):
        """Save settings and close dialog"""
        if self.ffmpeg_install_running:
            QMessageBox.warning(self, "FFmpeg Installation",
                                "Please wait for the FFmpeg install to finish.")
            return
        # PNG
        self.export_settings.png_compression = self.png_compression_spin.value()
        self.export_settings.png_full_resolution = self.png_full_res_check.isChecked()
        self.export_settings.png_full_scale_multiplier = self.png_full_res_multiplier_spin.value()
        
        # GIF
        self.export_settings.gif_fps = self.gif_fps_spin.value()
        self.export_settings.gif_colors = int(self.gif_colors_combo.currentText())
        self.export_settings.gif_scale = self.gif_scale_spin.value()
        self.export_settings.gif_dither = self.gif_dither_check.isChecked()
        self.export_settings.gif_optimize = self.gif_optimize_check.isChecked()
        self.export_settings.gif_loop = self.gif_loop_spin.value()
        
        # MOV
        codec_text = self.mov_codec_combo.currentText()
        self.export_settings.mov_codec = codec_text.split(' - ')[0] if ' - ' in codec_text else codec_text
        self.export_settings.mov_quality = self.mov_quality_combo.currentText().lower()
        self.export_settings.mov_include_audio = self.mov_include_audio_check.isChecked()
        self.export_settings.mov_full_resolution = self.mov_full_res_check.isChecked()
        self.export_settings.mov_full_scale_multiplier = self.mov_full_res_multiplier_spin.value()

        # WEBM
        webm_codec_text = self.webm_codec_combo.currentText()
        self.export_settings.webm_codec = (
            webm_codec_text.split(' - ')[0] if ' - ' in webm_codec_text else webm_codec_text
        )
        self.export_settings.webm_crf = self.webm_crf_spin.value()
        self.export_settings.webm_speed = self.webm_speed_spin.value()
        self.export_settings.webm_include_audio = self.webm_include_audio_check.isChecked()
        self.export_settings.webm_full_resolution = self.webm_full_res_check.isChecked()
        self.export_settings.webm_full_scale_multiplier = self.webm_full_res_multiplier_spin.value()

        # MP4
        mp4_codec_text = self.mp4_codec_combo.currentText()
        self.export_settings.mp4_codec = mp4_codec_text.split(' - ')[0] if ' - ' in mp4_codec_text else mp4_codec_text
        self.export_settings.mp4_crf = self.mp4_crf_spin.value()
        self.export_settings.mp4_preset = self.mp4_preset_combo.currentText().lower()
        self.export_settings.mp4_bitrate = self.mp4_bitrate_spin.value()
        self.export_settings.mp4_include_audio = self.mp4_include_audio_check.isChecked()
        self.export_settings.mp4_full_resolution = self.mp4_full_res_check.isChecked()
        self.export_settings.mp4_full_scale_multiplier = self.mp4_full_res_multiplier_spin.value()
        pixel_fmt_text = self.mp4_pixel_format_combo.currentText()
        self.export_settings.mp4_pixel_format = (
            pixel_fmt_text.split(' - ')[0] if ' - ' in pixel_fmt_text else pixel_fmt_text
        )
        self.export_settings.mp4_faststart = self.mp4_faststart_check.isChecked()

        self.export_settings.camera_zoom_to_cursor = self.camera_zoom_cursor_check.isChecked()
        self.export_settings.use_barebones_file_browser = self.barebones_browser_check.isChecked()
        self.export_settings.anchor_debug_logging = self.anchor_debug_check.isChecked()
        self.export_settings.update_source_json_on_save = self.update_source_json_check.isChecked()

        # PSD
        self.export_settings.psd_include_hidden = self.psd_hidden_check.isChecked()
        self.export_settings.psd_scale = self.psd_scale_spin.value()
        self.export_settings.psd_quality = self.psd_quality_combo.currentData()
        self.export_settings.psd_compression = self.psd_compression_combo.currentData()
        self.export_settings.psd_crop_canvas = self.psd_crop_check.isChecked()
        self.export_settings.psd_match_viewport = self.psd_match_viewport_check.isChecked()
        self.export_settings.psd_preserve_resolution = self.psd_full_res_check.isChecked()
        self.export_settings.psd_full_res_multiplier = self.psd_full_res_multiplier_spin.value()

        # Shader overrides
        shader_overrides = self.shader_tab.get_overrides()
        try:
            overrides_blob = json.dumps(shader_overrides)
        except Exception:
            overrides_blob = "{}"
        self.app_settings.setValue("shaders/overrides", overrides_blob)
        self.shader_registry.set_user_overrides(shader_overrides)

        self.export_settings.save()
        self._save_diagnostics_settings()
        self.accept()

    def update_ffmpeg_status(self):
        """Report FFmpeg availability and button text."""
        stored_path = self.app_settings.value('ffmpeg/path', '', type=str)
        ffmpeg_path = resolve_ffmpeg_path(stored_path)

        if stored_path and not ffmpeg_path:
            self.app_settings.remove('ffmpeg/path')

        if ffmpeg_path:
            path_text = Path(ffmpeg_path)
            self.ffmpeg_status_label.setText(f"FFmpeg ready at: {path_text}")
            self.ffmpeg_install_button.setText("Reinstall FFmpeg")
        else:
            self.ffmpeg_status_label.setText(
                "FFmpeg not detected. MOV/Video exports will remain disabled until it is installed."
            )
            self.ffmpeg_install_button.setText("Install FFmpeg")

        if not self.ffmpeg_install_running:
            self.ffmpeg_install_button.setEnabled(True)

    def start_ffmpeg_install(self):
        """Begin installing FFmpeg in the background."""
        if self.ffmpeg_install_running:
            return

        self.ffmpeg_install_running = True
        self.ffmpeg_progress.setValue(0)
        self.ffmpeg_progress.setVisible(True)
        self.ffmpeg_status_label.setText("Starting FFmpeg download...")
        self.ffmpeg_install_button.setEnabled(False)
        self.reset_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.save_btn.setEnabled(False)

        self.ffmpeg_worker = FFmpegInstallWorker()
        self.ffmpeg_thread = QThread(self)
        self.ffmpeg_worker.moveToThread(self.ffmpeg_thread)

        self.ffmpeg_thread.started.connect(self.ffmpeg_worker.run)
        self.ffmpeg_worker.statusChanged.connect(self.ffmpeg_status_label.setText)
        self.ffmpeg_worker.progressChanged.connect(self.ffmpeg_progress.setValue)
        self.ffmpeg_worker.finished.connect(self.on_ffmpeg_install_finished)
        self.ffmpeg_worker.finished.connect(self.ffmpeg_thread.quit)
        self.ffmpeg_thread.finished.connect(self._cleanup_ffmpeg_thread)
        self.ffmpeg_thread.start()

    def on_ffmpeg_install_finished(self, success: bool, payload: str):
        """Handle completion of the FFmpeg installer."""
        self.ffmpeg_install_running = False
        self.ffmpeg_progress.setVisible(False)
        self.reset_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        self.save_btn.setEnabled(True)

        if success:
            self.app_settings.setValue('ffmpeg/path', payload)
            self.ffmpeg_status_label.setText(f"FFmpeg installed at: {payload}")
        else:
            self.ffmpeg_status_label.setText(f"FFmpeg install failed: {payload}")

        self.update_ffmpeg_status()

    def _cleanup_ffmpeg_thread(self):
        """Release worker/thread once the install process exits."""
        if self.ffmpeg_worker:
            self.ffmpeg_worker.deleteLater()
            self.ffmpeg_worker = None
        if self.ffmpeg_thread:
            self.ffmpeg_thread.deleteLater()
            self.ffmpeg_thread = None

    def reject(self):
        if self.ffmpeg_install_running:
            QMessageBox.warning(self, "FFmpeg Installation",
                                "Please wait for the FFmpeg install to finish.")
            return
        super().reject()

    def closeEvent(self, event):
        if self.ffmpeg_install_running:
            QMessageBox.warning(self, "FFmpeg Installation",
                                "Please wait for the FFmpeg install to finish.")
            event.ignore()
            return
        super().closeEvent(event)

    def _update_psd_full_res_controls(self):
        """Enable/disable PSD multiplier control based on checkbox."""
        enabled = self.psd_full_res_check.isChecked()
        self.psd_full_res_multiplier_spin.setEnabled(enabled)

    def _update_png_full_res_controls(self):
        """Enable/disable PNG multiplier control based on checkbox."""
        enabled = self.png_full_res_check.isChecked()
        self.png_full_res_multiplier_spin.setEnabled(enabled)

    def _update_mov_full_res_controls(self):
        """Enable/disable MOV multiplier control based on checkbox."""
        enabled = self.mov_full_res_check.isChecked()
        self.mov_full_res_multiplier_spin.setEnabled(enabled)

    def _update_webm_full_res_controls(self):
        """Enable/disable WEBM multiplier control based on checkbox."""
        enabled = self.webm_full_res_check.isChecked()
        self.webm_full_res_multiplier_spin.setEnabled(enabled)

    def _update_mp4_full_res_controls(self):
        """Enable/disable MP4 multiplier control based on checkbox."""
        enabled = self.mp4_full_res_check.isChecked()
        self.mp4_full_res_multiplier_spin.setEnabled(enabled)
