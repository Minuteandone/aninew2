"""
Main Window
The main application window that ties everything together
"""

import sys
import os
import re
import json
import math
import subprocess
import tempfile
import shutil
import importlib
import importlib.util
import types
import faulthandler
import copy
import difflib
import struct
import random
import xml.etree.ElementTree as ET
from glob import glob
from pathlib import Path
from typing import Optional, Dict, List, Set, Tuple, Any

import numpy as np
from dataclasses import dataclass, replace
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QMessageBox,
    QSplitter, QProgressDialog, QDialog, QInputDialog
)
from PyQt6.QtCore import Qt, QSettings, QTimer, QEvent
from PyQt6.QtGui import QSurfaceFormat, QColor, QShortcut, QKeySequence, QPixmap, QImage
from PyQt6.QtWidgets import QGraphicsDropShadowEffect
from PIL import Image
from OpenGL.GL import *
import soundfile as sf

from core.data_structures import AnimationData, LayerData, KeyframeData, SpriteInfo
from core.animation_player import AnimationPlayer
from core.texture_atlas import TextureAtlas
from core.audio_manager import AudioManager
from utils.buddy_manifest import BuddyManifest
from renderer.opengl_widget import OpenGLAnimationWidget
from renderer.sprite_renderer import BlendMode
from .log_widget import LogWidget
from .timeline import TimelineWidget
from .control_panel import ControlPanel
from .layer_panel import LayerPanel
from .settings_dialog import SettingsDialog, ExportSettings
from .monster_browser_dialog import (
    MonsterBrowserDialog,
    MonsterBrowserEntry,
    MonsterVariantOption,
)
from .sprite_workshop_dialog import SpriteWorkshopDialog
from .sprite_picker_dialog import SpritePickerDialog
from utils.diagnostics import DiagnosticsManager, DiagnosticsConfig
from utils.ffmpeg_installer import resolve_ffmpeg_path
from utils.pytoshop_installer import PytoshopInstaller, PythonPackageInstaller
from utils.shader_registry import ShaderRegistry


@dataclass
class SpriteReplacementRecord:
    """Tracks a custom sprite override applied in the Sprite Workshop."""
    atlas_key: str
    sprite_name: str
    source_path: Optional[str]
    applied_at: str
from Resources.bin2json.parse_costume_bin import parse_costume_file


@dataclass
class AnimationFileEntry:
    """Metadata for indexed BIN/JSON files."""
    name: str
    relative_path: str
    full_path: str

    @property
    def is_json(self) -> bool:
        return self.full_path.lower().endswith('.json')

    @property
    def is_bin(self) -> bool:
        return self.full_path.lower().endswith('.bin')

    def normalized_path(self) -> str:
        """Return a normalized absolute path for quick comparisons."""
        return os.path.normcase(os.path.normpath(self.full_path))


@dataclass
class MonsterFileRecord:
    """Aggregated BIN/JSON paths for a specific monster stem (e.g., monster_bowgart_fire)."""

    stem: str
    relative_path: str
    json_path: Optional[str] = None
    bin_path: Optional[str] = None

    def has_source(self) -> bool:
        return bool(self.json_path or self.bin_path)


@dataclass
class CostumeEntry:
    """Metadata for a costume definition and its backing files."""
    key: str
    display_name: str
    bin_path: Optional[str] = None
    json_path: Optional[str] = None
    bin_priority: int = 1_000_000
    json_priority: int = 1_000_000

    @property
    def source_path(self) -> Optional[str]:
        return self.json_path or self.bin_path


class MSMAnimationViewer(QMainWindow):
    """Main application window"""
    
    def __init__(self):
        super().__init__()

        faulthandler.enable()
        self.settings = QSettings('MSMAnimationViewer', 'Settings')
        self.project_root = Path(__file__).parent.parent
        self.shader_registry = ShaderRegistry(self.project_root)
        shader_blob = self.settings.value('shaders/overrides', '', type=str)
        shader_overrides: Dict[str, Any] = {}
        if shader_blob:
            try:
                shader_overrides = json.loads(shader_blob)
            except (TypeError, ValueError):
                shader_overrides = {}
        self.shader_registry.set_user_overrides(shader_overrides)
        self.game_path: str = self.settings.value('game_path', '')
        self.shader_registry.set_game_path(self.game_path or None)
        self.sync_audio_to_bpm: bool = True
        self.pitch_shift_enabled: bool = False
        self.chipmunk_mode: bool = False
        self._load_audio_preferences_from_storage()
        self.bin2json_path: str = ""
        
        self.current_json_data: Optional[Dict] = None
        self.original_json_data: Optional[Dict] = None
        self.current_blend_version: int = 1
        self.current_animation_index: int = 0
        self.file_index: List[AnimationFileEntry] = []
        self.filtered_file_index: List[AnimationFileEntry] = []
        self.monster_file_lookup: Dict[str, MonsterFileRecord] = {}
        self.current_search_text: str = ""
        
        # Export settings
        self.export_settings = ExportSettings()
        self.anchor_debug_enabled: bool = bool(self.export_settings.anchor_debug_logging)
        self.audio_library: Dict[str, List[str]] = {}
        self.current_audio_path: Optional[str] = None
        self.current_animation_name: Optional[str] = None
        self.current_json_path: Optional[str] = None
        self.costume_entries: List["CostumeEntry"] = []
        self.costume_entry_map: Dict[str, "CostumeEntry"] = {}
        self.costume_cache: Dict[str, Dict[str, Any]] = {}
        self.attachment_animation_cache: Dict[str, Dict[str, Any]] = {}
        self.canonical_clone_cache: Dict[str, List[Dict[str, Any]]] = {}
        self.current_animation_embedded_clones: Optional[List[Dict[str, Any]]] = None
        self.canonical_layer_names: Set[str] = set()
        self.active_costume_key: Optional[str] = None
        self.base_layer_cache: Optional[List[LayerData]] = None
        self.base_texture_atlases: List[TextureAtlas] = []
        self.costume_atlas_cache: Dict[str, TextureAtlas] = {}
        self.active_costume_attachments: List[Dict[str, Any]] = []
        self.costume_sheet_aliases: Dict[str, List[str]] = {}
        self.current_base_bpm: float = 120.0
        self.current_bpm: float = 120.0
        self.animation_bpm_overrides: Dict[str, float] = {}
        self.monster_base_bpm_overrides: Dict[str, float] = {}
        self._load_base_bpm_overrides()
        self.layer_visibility_cache: Dict[str, Dict[int, bool]] = {}
        self._default_layer_order: List[int] = []
        self._default_layer_visibility: Dict[int, bool] = {}
        self._default_hidden_layer_ids: Set[int] = set()
        self.pose_influence_mode: str = "current"
        self.layer_source_lookup: Dict[int, Dict[str, Any]] = {}
        self.source_atlas_lookup: Dict[Any, TextureAtlas] = {}
        self._pose_baseline_player: Optional[AnimationPlayer] = None
        self._pose_baseline_lookup: Dict[int, LayerData] = {}
        self._history_stack: List[Dict[str, Any]] = []
        self._history_redo_stack: List[Dict[str, Any]] = []
        self._pending_keyframe_action: Optional[Dict[str, Any]] = None
        self._timeline_user_scrubbing: bool = False
        self._resume_audio_after_scrub: bool = False
        self.solid_bg_enabled: bool = self.settings.value('export/solid_bg_enabled', False, type=bool)
        solid_bg_hex = self.settings.value('export/solid_bg_color', '#000000FF', type=str) or '#000000FF'
        self.solid_bg_color: Tuple[int, int, int, int] = self._parse_rgba_hex(solid_bg_hex, (0, 0, 0, 255))
        self.force_opaque: bool = self.settings.value('viewer/force_opaque', False, type=bool)
        self._layer_thumbnail_cache: Dict[str, Optional[QPixmap]] = {}
        self._atlas_image_cache: Dict[str, Optional[Image.Image]] = {}
        self._layer_sprite_preview_state: Dict[int, Optional[str]] = {}
        self._selected_marker_times: Set[float] = set()
        self._atlas_original_image_cache: Dict[str, Optional[Image.Image]] = {}
        self._atlas_modified_images: Dict[str, Image.Image] = {}
        self._sprite_replacements: Dict[Tuple[str, str], SpriteReplacementRecord] = {}
        self._atlas_dirty_flags: Dict[str, bool] = {}
        self._sprite_workshop_dialog: Optional[SpriteWorkshopDialog] = None
        self._keyframe_clipboard: Optional[Dict[str, Any]] = None
        self._hang_watchdog_active: bool = False
        self.buddy_audio_tracks: Dict[str, str] = {}
        self.buddy_audio_tracks_normalized: Dict[str, str] = {}
        self.build_version: str = "0.54hotfix"
        self._windowed_geometry: Optional[bytes] = None
        self._fullscreen_active = False
        self._pytoshop = None
        self._rev6_anim_module = None
        self._diagnostics_config = DiagnosticsConfig()
        self.diagnostics: Optional[DiagnosticsManager] = None

        self.init_ui()
        self._apply_anchor_logging_preferences()
        self._setup_shortcuts()
        self.audio_manager = AudioManager(self)
        self.audio_manager.set_volume(self.control_panel.audio_volume_slider.value())
        self.audio_manager.set_enabled(self.control_panel.audio_enable_checkbox.isChecked())
        self._apply_audio_preferences_to_controls()
        self.selected_layer_ids: Set[int] = set()
        self.primary_selected_layer_id: Optional[int] = None
        self.selection_lock_enabled: bool = False
        self.load_settings()
        
        # Find bin2json script
        self.find_bin2json()
    
    def init_ui(self):
        """Initialize the user interface"""
        self.setWindowTitle("My Singing Monsters Animation Viewer")
        self.setGeometry(100, 100, 1400, 900)
        
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout(central_widget)
        
        # Top toolbar
        toolbar_layout = QHBoxLayout()
        
        self.path_label = QLabel("Game Path: Not Set")
        toolbar_layout.addWidget(self.path_label)
        
        browse_btn = QPushButton("Browse Game Path")
        browse_btn.clicked.connect(self.browse_game_path)
        toolbar_layout.addWidget(browse_btn)
        
        toolbar_layout.addStretch()
        
        sprite_workshop_btn = QPushButton("Sprite Workshop")
        sprite_workshop_btn.clicked.connect(self.show_sprite_workshop)
        toolbar_layout.addWidget(sprite_workshop_btn)

        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(self.show_settings)
        toolbar_layout.addWidget(settings_btn)
        
        main_layout.addLayout(toolbar_layout)
        
        # Splitter for main content
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(2)
        
        # Left panel - Controls
        self.control_panel = ControlPanel()
        self.control_panel.set_bpm_value(self.current_bpm)
        self.connect_control_panel_signals()
        self.control_panel.set_pose_controls_enabled(False)
        self.control_panel.set_sprite_tools_enabled(False)
        self.control_panel.set_solid_bg_enabled(self.solid_bg_enabled)
        self.control_panel.set_solid_bg_color(self.solid_bg_color)
        self.control_panel.set_remove_transparency_enabled(self.force_opaque)
        splitter.addWidget(self.control_panel)
        
        # Center - OpenGL viewer
        self.gl_widget = OpenGLAnimationWidget(shader_registry=self.shader_registry)
        self.gl_widget.set_costume_pivot_adjustment_enabled(False)
        self.gl_widget.set_zoom_to_cursor(self.export_settings.camera_zoom_to_cursor)
        # Apply saved viewer flags
        self.gl_widget.set_force_opaque(self.force_opaque)
        self.gl_widget.animation_time_changed.connect(self.on_animation_time_changed)
        self.gl_widget.animation_looped.connect(self.on_animation_looped)
        self.gl_widget.playback_state_changed.connect(self.on_playback_state_changed)
        self.gl_widget.transform_action_committed.connect(self._record_transform_action)
        splitter.addWidget(self.gl_widget)
        
        # Right panel - Layer visibility
        self.layer_panel = LayerPanel()
        self.connect_layer_panel_signals()
        
        # Set size constraints for right panel
        self.layer_panel.setMinimumWidth(200)
        self.layer_panel.setMaximumWidth(350)
        
        splitter.addWidget(self.layer_panel)
        
        splitter.setCollapsible(0, True)
        splitter.setCollapsible(1, False)
        splitter.setCollapsible(2, True)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([520, 860, 320])
        
        # Timeline and log inside a vertical splitter
        self.timeline = TimelineWidget()
        self.connect_timeline_signals()
        self.log_widget = LogWidget()
        self._init_diagnostics()
        
        content_container = QWidget()
        content_layout = QVBoxLayout(content_container)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(6)
        content_layout.addWidget(splitter, stretch=1)
        content_layout.addWidget(self.timeline)
        
        log_splitter = QSplitter(Qt.Orientation.Vertical)
        log_splitter.addWidget(content_container)
        log_splitter.addWidget(self.log_widget)
        log_splitter.setStretchFactor(0, 3)
        log_splitter.setStretchFactor(1, 1)
        log_splitter.setCollapsible(1, True)
        
        main_layout.addWidget(log_splitter, stretch=1)
        
        self.log_widget.log("Application started", "INFO")
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)

    def _setup_shortcuts(self):
        """Configure application-wide shortcuts."""
        self.fullscreen_shortcut = QShortcut(QKeySequence(Qt.Key.Key_F11), self)
        self.fullscreen_shortcut.activated.connect(self.toggle_fullscreen)
        undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        undo_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        undo_shortcut.activated.connect(self._handle_undo_shortcut)
        redo_shortcut = QShortcut(QKeySequence.StandardKey.Redo, self)
        redo_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        redo_shortcut.activated.connect(self._handle_redo_shortcut)
        # Ensure Ctrl+Shift+Z also triggers redo for platforms where StandardKey maps differently
        redo_combo = QShortcut(QKeySequence("Ctrl+Shift+Z"), self)
        redo_combo.setContext(Qt.ShortcutContext.ApplicationShortcut)
        redo_combo.activated.connect(self._handle_redo_shortcut)
        copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self)
        copy_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        copy_shortcut.activated.connect(self.copy_selected_keyframes)
        paste_shortcut = QShortcut(QKeySequence.StandardKey.Paste, self)
        paste_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        paste_shortcut.activated.connect(self.paste_copied_keyframes)
        self._undo_shortcut = undo_shortcut
        self._redo_shortcut = redo_shortcut
        self._redo_shift_shortcut = redo_combo
        self._copy_shortcut = copy_shortcut
        self._paste_shortcut = paste_shortcut

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.ShortcutOverride and self._is_ctrl_shift_z(event):
            event.accept()
            self._handle_redo_shortcut()
            return True
        return super().eventFilter(watched, event)

    def _handle_undo_shortcut(self):
        """Undo the most recent edit action."""
        if not self._undo_history_action():
            self.log_widget.log("Nothing to undo.", "INFO")

    def _handle_redo_shortcut(self):
        """Redo the most recently undone edit action."""
        if not self._redo_history_action():
            self.log_widget.log("Nothing to redo.", "INFO")

    def _is_ctrl_shift_z(self, event) -> bool:
        if event.key() != Qt.Key.Key_Z:
            return False
        modifiers = event.modifiers()
        if not (modifiers & Qt.KeyboardModifier.ControlModifier):
            return False
        if not (modifiers & Qt.KeyboardModifier.ShiftModifier):
            return False
        if modifiers & Qt.KeyboardModifier.AltModifier:
            return False
        if modifiers & Qt.KeyboardModifier.MetaModifier:
            return False
        return True

    def _undo_history_action(self, required_type: Optional[str] = None) -> bool:
        if not self._history_stack:
            return False
        action = self._history_stack[-1]
        if required_type and action['type'] != required_type:
            return False
        action = self._history_stack.pop()
        self._apply_history_action(action, undo=True)
        self._history_redo_stack.append(action)
        self._update_keyframe_history_controls()
        return True

    def _redo_history_action(self, required_type: Optional[str] = None) -> bool:
        if not self._history_redo_stack:
            return False
        action = self._history_redo_stack[-1]
        if required_type and action['type'] != required_type:
            return False
        action = self._history_redo_stack.pop()
        self._apply_history_action(action, undo=False)
        self._history_stack.append(action)
        self._update_keyframe_history_controls()
        return True

    def _apply_history_action(self, action: Dict[str, Any], *, undo: bool):
        action_type = action.get('type')
        label = action.get('label') or action_type or "edit"
        if action_type == 'transform':
            state = action['before'] if undo else action['after']
            self.gl_widget.apply_transform_snapshot(state)
            self.update_offset_display()
            message = "Undid" if undo else "Redid"
            self.log_widget.log(f"{message} {label}", "INFO")
            return
        snapshot = action['before'] if undo else action['after']
        self._apply_keyframe_snapshot(snapshot)
        self._refresh_timeline_keyframes()
        message = "Undid" if undo else "Redid"
        self.log_widget.log(f"{message} {label}", "INFO")

    def _push_history_action(self, action: Dict[str, Any]):
        self._history_stack.append(action)
        self._history_redo_stack.clear()
        self._update_keyframe_history_controls()

    def _record_transform_action(self, action: Dict[str, Any]):
        if not action:
            return
        payload = dict(action)
        payload['type'] = 'transform'
        payload.setdefault('label', "sprite transform")
        self._push_history_action(payload)

    def _apply_anchor_logging_preferences(self):
        """Toggle renderer anchor logging based on user preference."""
        self.anchor_debug_enabled = bool(self.export_settings.anchor_debug_logging)
        if hasattr(self, "gl_widget") and self.gl_widget:
            self.gl_widget.set_anchor_logging_enabled(self.anchor_debug_enabled)

    def toggle_fullscreen(self):
        """Toggle between windowed and borderless fullscreen modes."""
        if self._fullscreen_active:
            self._exit_fullscreen()
        else:
            self._enter_fullscreen()

    def _enter_fullscreen(self):
        if self._fullscreen_active:
            return
        self._windowed_geometry = self.saveGeometry()
        self._fullscreen_active = True
        self.showFullScreen()

    def _exit_fullscreen(self):
        if not self._fullscreen_active:
            return
        self._fullscreen_active = False
        self.showNormal()
        if self._windowed_geometry:
            self.restoreGeometry(self._windowed_geometry)

    def _on_user_toggle_diagnostics(self, enabled: bool):
        self._diagnostics_config.enabled = enabled
        self.settings.setValue('diagnostics/enabled', enabled)
        if self.diagnostics:
            self.diagnostics.apply_config(self._diagnostics_config)
        state = "enabled" if enabled else "disabled"
        level = "SUCCESS" if enabled else "INFO"
        self.log_widget.log(f"Diagnostics logging {state}", level)

    def _refresh_diagnostics_overlay(self):
        if hasattr(self, "diagnostics"):
            self.diagnostics.refresh_layer_statuses()
            self.log_widget.log("Diagnostics overlay refreshed", "INFO")

    def _export_diagnostics_log(self):
        if not hasattr(self, "diagnostics"):
            return
        target = self._diagnostics_config.export_path
        if not target:
            target, _ = QFileDialog.getSaveFileName(
                self,
                "Export Diagnostics Log",
                str(Path.home() / "diagnostics.log"),
                "Log Files (*.log *.txt);;All Files (*)"
            )
        if not target:
            return
        success, message = self.diagnostics.export_to_file(target)
        level = "SUCCESS" if success else "ERROR"
        self.log_widget.log(message, level)
        if success:
            self._diagnostics_config.export_path = target
            self.settings.setValue('diagnostics/export_path', target)
    
    def connect_control_panel_signals(self):
        """Connect control panel signals"""
        self.control_panel.bin_selected.connect(self.on_bin_selected)
        self.control_panel.convert_bin_clicked.connect(self.convert_bin_to_json)
        self.control_panel.refresh_files_clicked.connect(self.refresh_file_list)
        self.control_panel.file_search_changed.connect(self.on_file_search_changed)
        self.control_panel.animation_selected.connect(self.on_animation_selected)
        self.control_panel.costume_selected.connect(self.on_costume_selected)
        self.control_panel.costume_convert_clicked.connect(self.convert_selected_costume)
        self.control_panel.scale_changed.connect(self.on_scale_changed)
        self.control_panel.fps_changed.connect(self.on_fps_changed)
        self.control_panel.position_scale_changed.connect(self.on_position_scale_changed)
        self.control_panel.position_scale_slider_changed.connect(self.on_position_scale_slider_changed)
        self.control_panel.base_world_scale_changed.connect(self.on_base_world_scale_changed)
        self.control_panel.base_world_scale_slider_changed.connect(self.on_base_world_scale_slider_changed)
        self.control_panel.translation_sensitivity_changed.connect(self.on_translation_sensitivity_changed)
        self.control_panel.rotation_sensitivity_changed.connect(self.on_rotation_sensitivity_changed)
        self.control_panel.rotation_overlay_size_changed.connect(self.on_rotation_overlay_size_changed)
        self.control_panel.rotation_gizmo_toggled.connect(self.toggle_rotation_gizmo)
        self.control_panel.anchor_overlay_toggled.connect(self.toggle_anchor_overlay)
        self.control_panel.parent_overlay_toggled.connect(self.toggle_parent_overlay)
        self.control_panel.anchor_drag_precision_changed.connect(self.on_anchor_drag_precision_changed)
        self.control_panel.bpm_value_changed.connect(self.on_bpm_value_changed)
        self.control_panel.sync_audio_to_bpm_toggled.connect(self.on_sync_audio_to_bpm_toggled)
        self.control_panel.pitch_shift_toggled.connect(self.on_pitch_shift_toggled)
        self.control_panel.bpm_reset_requested.connect(self.on_reset_bpm_requested)
        self.control_panel.base_bpm_lock_requested.connect(self.on_lock_base_bpm_requested)
        self.control_panel.anchor_bias_x_changed.connect(self.on_anchor_bias_x_changed)
        self.control_panel.anchor_bias_y_changed.connect(self.on_anchor_bias_y_changed)
        self.control_panel.local_position_multiplier_changed.connect(self.on_local_position_multiplier_changed)
        self.control_panel.parent_mix_changed.connect(self.on_parent_mix_changed)
        self.control_panel.rotation_bias_changed.connect(self.on_rotation_bias_changed)
        self.control_panel.scale_bias_x_changed.connect(self.on_scale_bias_x_changed)
        self.control_panel.scale_bias_y_changed.connect(self.on_scale_bias_y_changed)
        self.control_panel.world_offset_x_changed.connect(self.on_world_offset_x_changed)
        self.control_panel.world_offset_y_changed.connect(self.on_world_offset_y_changed)
        self.control_panel.trim_shift_multiplier_changed.connect(self.on_trim_shift_multiplier_changed)
        self.control_panel.reset_camera_clicked.connect(self.reset_camera)
        self.control_panel.fit_to_view_clicked.connect(self.fit_to_view)
        self.control_panel.show_bones_toggled.connect(self.toggle_bone_overlay)
        self.control_panel.tweening_toggled.connect(self.on_tweening_toggled)
        # Glitch controls
        self.control_panel.glitch_jitter_toggled.connect(self.on_glitch_jitter_toggled)
        self.control_panel.glitch_jitter_amount_changed.connect(self.on_glitch_jitter_amount_changed)
        self.control_panel.glitch_sprite_toggled.connect(self.on_glitch_sprite_toggled)
        self.control_panel.glitch_sprite_chance_changed.connect(self.on_glitch_sprite_chance_changed)
        self.control_panel.reset_offsets_clicked.connect(self.reset_sprite_offsets)
        self.control_panel.export_frame_clicked.connect(self.export_current_frame)
        self.control_panel.export_frames_sequence_clicked.connect(self.export_animation_frames_as_png)
        self.control_panel.export_psd_clicked.connect(self.export_as_psd)
        self.control_panel.export_mov_clicked.connect(self.export_as_mov)
        self.control_panel.export_mp4_clicked.connect(self.export_as_mp4)
        self.control_panel.export_webm_clicked.connect(self.export_as_webm)
        self.control_panel.export_gif_clicked.connect(self.export_as_gif)
        self.control_panel.credits_clicked.connect(self.show_credits)
        self.control_panel.monster_browser_requested.connect(self.open_monster_browser)
        self.control_panel.solid_bg_enabled_changed.connect(self.on_solid_bg_enabled_changed)
        self.control_panel.solid_bg_color_changed.connect(self.on_solid_bg_color_changed)
        self.control_panel.solid_bg_auto_requested.connect(self.on_auto_background_color_requested)
        self.control_panel.remove_transparency_toggled.connect(self.on_remove_transparency_toggled)
        self.control_panel.audio_enabled_changed.connect(self.on_audio_enabled_changed)
        self.control_panel.audio_volume_changed.connect(self.on_audio_volume_changed)
        self.control_panel.antialias_toggled.connect(self.toggle_antialiasing)
        self.control_panel.save_offsets_clicked.connect(self.save_layer_offsets)
        self.control_panel.load_offsets_clicked.connect(self.load_layer_offsets)
        self.control_panel.nudge_x_changed.connect(self.on_nudge_x)
        self.control_panel.nudge_y_changed.connect(self.on_nudge_y)
        self.control_panel.nudge_rotation_changed.connect(self.on_nudge_rotation)
        self.control_panel.nudge_scale_x_changed.connect(self.on_nudge_scale_x)
        self.control_panel.nudge_scale_y_changed.connect(self.on_nudge_scale_y)
        self.control_panel.scale_gizmo_toggled.connect(self.toggle_scale_gizmo)
        self.control_panel.scale_gizmo_mode_changed.connect(self.on_scale_mode_changed)
        self.control_panel.diagnostics_enabled_changed.connect(self._on_user_toggle_diagnostics)
        self.control_panel.diagnostics_refresh_requested.connect(self._refresh_diagnostics_overlay)
        self.control_panel.diagnostics_export_requested.connect(self._export_diagnostics_log)
        self.control_panel.pose_record_clicked.connect(self.on_record_pose_clicked)
        self.control_panel.pose_mode_changed.connect(self.on_pose_influence_changed)
        self.control_panel.pose_reset_clicked.connect(self.on_reset_pose_clicked)
        self.control_panel.keyframe_undo_clicked.connect(self.undo_keyframe_action)
        self.control_panel.keyframe_redo_clicked.connect(self.redo_keyframe_action)
        self.control_panel.keyframe_delete_others_clicked.connect(self.delete_other_keyframes)
        self.control_panel.extend_duration_clicked.connect(self.extend_animation_duration_dialog)
        self.control_panel.save_animation_clicked.connect(self.save_animation_to_file)
        self.control_panel.export_animation_bin_clicked.connect(self.export_animation_to_bin)
        self.control_panel.load_animation_clicked.connect(self.load_saved_animation)
        self.control_panel.sprite_assign_clicked.connect(self.assign_sprite_to_keyframes)
        self.control_panel.set_barebones_file_mode(self.export_settings.use_barebones_file_browser)
        self._update_keyframe_history_controls()

    
    def connect_layer_panel_signals(self):
        """Connect layer panel signals"""
        self.layer_panel.layer_visibility_changed.connect(self.toggle_layer_visibility)
        self.layer_panel.layer_visibility_changed.connect(self._on_layer_visibility_logged)
        self.layer_panel.layer_selection_changed.connect(self.on_layer_selection_changed)
        self.layer_panel.selection_lock_toggled.connect(self.on_selection_lock_toggled)
        self.layer_panel.all_layers_deselected.connect(self.on_layer_selection_cleared)
        self.layer_panel.color_changed.connect(self.on_layer_color_changed)
        self.layer_panel.color_reset_requested.connect(self.on_layer_color_reset)
        self.layer_panel.layer_order_changed.connect(self.on_layer_order_changed)
        self.layer_panel.reset_layer_order_requested.connect(self.reset_layer_order_to_default)
        self.layer_panel.reset_layer_visibility_requested.connect(self.reset_layer_visibility_to_default)
        self.layer_panel.sprite_assign_requested.connect(
            lambda layer_id: self.assign_sprite_to_keyframes([layer_id])
        )

    def _on_layer_visibility_logged(self, layer: LayerData, state: int):
        if not hasattr(self, "diagnostics"):
            return
        if state == Qt.CheckState.Checked:
            text = "visible"
        elif state == Qt.CheckState.PartiallyChecked:
            text = "partially visible"
        else:
            text = "hidden"
        self.diagnostics.log_visibility(
            f"Layer '{layer.name}' set {text}", layer_id=layer.layer_id
        )
    
    def connect_timeline_signals(self):
        """Connect timeline signals"""
        self.timeline.play_toggled.connect(self.toggle_playback)
        self.timeline.loop_toggled.connect(self.toggle_loop)
        self.timeline.time_changed.connect(self.on_timeline_changed)
        self.timeline.keyframe_marker_clicked.connect(self.on_keyframe_marker_clicked)
        self.timeline.keyframe_marker_remove_requested.connect(self.on_keyframe_marker_remove_requested)
        self.timeline.keyframe_marker_dragged.connect(self.on_keyframe_marker_dragged)
        self.timeline.keyframe_selection_changed.connect(self.on_keyframe_selection_changed)
        self.timeline.timeline_slider.sliderPressed.connect(self.on_timeline_slider_pressed)
        self.timeline.timeline_slider.sliderReleased.connect(self.on_timeline_slider_released)

    def _init_diagnostics(self):
        self.diagnostics = DiagnosticsManager(self.layer_panel, self.log_widget, self)
        self._diagnostics_config = DiagnosticsConfig()
        self._load_diagnostics_settings()

    def _load_diagnostics_settings(self):
        s = self.settings
        get_bool = lambda key, default: s.value(f"diagnostics/{key}", default, type=bool)
        get_int = lambda key, default: s.value(f"diagnostics/{key}", default, type=int)
        get_float = lambda key, default: s.value(f"diagnostics/{key}", default, type=float)
        get_str = lambda key, default: s.value(f"diagnostics/{key}", default, type=str)

        cfg = DiagnosticsConfig(
            enabled=get_bool("enabled", False),
            highlight_layers=get_bool("highlight_layers", True),
            throttle_updates=get_bool("throttle_updates", True),
            log_clone_events=get_bool("log_clone_events", True),
            log_canonical_events=get_bool("log_canonical_events", True),
            log_remap_events=get_bool("log_remap_events", False),
            log_sheet_events=get_bool("log_sheet_events", False),
            log_visibility_events=get_bool("log_visibility_events", False),
            log_shader_events=get_bool("log_shader_events", False),
            log_color_events=get_bool("log_color_events", False),
            log_attachment_events=get_bool("log_attachment_events", False),
            include_debug_payloads=get_bool("include_debug_payloads", False),
            max_entries=get_int("max_entries", 2000),
            update_interval_ms=get_int("update_interval_ms", 500),
            layer_status_duration_sec=get_float("layer_status_duration_sec", 6.0),
            rate_limit_per_sec=get_int("rate_limit_per_sec", 120),
            minimum_severity=get_str("minimum_severity", "INFO"),
            auto_export_enabled=get_bool("auto_export_enabled", False),
            auto_export_interval_sec=get_int("auto_export_interval_sec", 120),
            export_path=get_str("export_path", ""),
        )
        self._diagnostics_config = cfg
        self.diagnostics.apply_config(cfg)
        self.control_panel.set_diagnostics_enabled(cfg.enabled)
    
    def find_bin2json(self):
        """Find the bin2json script"""
        script_dir = Path(__file__).parent.parent
        bin2json_path = script_dir / "Resources" / "bin2json" / "rev6-2-json.py"
        
        if bin2json_path.exists():
            self.bin2json_path = str(bin2json_path)
            self.log_widget.log(f"Found bin2json script: {self.bin2json_path}", "SUCCESS")
        else:
            self.log_widget.log("bin2json script not found", "WARNING")

    def build_audio_library(self):
        """Index audio/music files under the selected game path."""
        self.audio_library.clear()
        if not self.game_path:
            return
        music_dir = os.path.join(self.game_path, "data", "audio", "music")
        if not os.path.exists(music_dir):
            self.log_widget.log(f"Music folder not found: {music_dir}", "WARNING")
            return

        total_files = 0
        for root, _, files in os.walk(music_dir):
            for file in files:
                lower = file.lower()
                if not (lower.endswith('.ogg') or lower.endswith('.wav') or lower.endswith('.mp3')):
                    continue
                key = self._normalize_audio_key(Path(file).stem)
                if not key:
                    continue
                full_path = os.path.join(root, file)
                paths = self.audio_library.setdefault(key, [])
                if full_path not in paths:
                    paths.append(full_path)
                    total_files += 1
        self.log_widget.log(f"Indexed {total_files} music clips", "INFO")
        if self.current_animation_name:
            self.load_audio_for_animation(self.current_animation_name)
        self._load_buddy_audio_tracks()

    def _load_buddy_audio_tracks(self):
        """Parse buddy manifests (001_*.bin) to map animations directly to audio files."""
        self.buddy_audio_tracks.clear()
        self.buddy_audio_tracks_normalized.clear()
        if not self.game_path:
            return
        xml_bin_dir = os.path.join(self.game_path, "data", "xml_bin")
        if not os.path.isdir(xml_bin_dir):
            self.log_widget.log(f"Buddy manifest folder missing: {xml_bin_dir}", "WARNING")
            return

        def is_buddy_manifest(path: str) -> bool:
            try:
                with open(path, "rb") as handle:
                    header = handle.read(4)
                    if len(header) < 4:
                        return False
                    (length,) = struct.unpack("<I", header)
                    if length <= 0 or length > 0x100:
                        return False
                    sig_bytes = handle.read(max(length - 1, 0))
                    signature = sig_bytes.decode("ascii", errors="ignore").strip().lower()
                    return signature == "budd"
            except Exception:
                return False

        manifest_paths = [
            path for path in sorted(glob(os.path.join(xml_bin_dir, "*.bin")))
            if is_buddy_manifest(path)
        ]
        if not manifest_paths:
            self.log_widget.log("No buddy manifest files found in xml_bin", "INFO")
            return

        data_root = os.path.join(self.game_path, "data")
        total_tracks = 0
        parsed_files = 0
        for manifest_path in manifest_paths:
            try:
                manifest = BuddyManifest.from_file(manifest_path)
            except Exception as exc:
                self.log_widget.log(
                    f"Failed to parse {os.path.basename(manifest_path)}: {exc}",
                    "WARNING"
                )
                continue

            parsed_files += 1
            for track_name, rel_audio in manifest.iter_audio_links():
                if not rel_audio:
                    self.log_widget.log(
                        f"Manifest {manifest.source_path.name} missing audio for track '{track_name}'",
                        "WARNING"
                    )
                    continue
                abs_path = os.path.join(data_root, rel_audio.replace("/", os.sep))
                normalized_name = self._normalize_audio_key(track_name)
                if track_name not in self.buddy_audio_tracks:
                    self.buddy_audio_tracks[track_name] = abs_path
                    total_tracks += 1
                if normalized_name and normalized_name not in self.buddy_audio_tracks_normalized:
                    self.buddy_audio_tracks_normalized[normalized_name] = abs_path

        if parsed_files:
            self.log_widget.log(
                f"Loaded {total_tracks} buddy audio links from {parsed_files} manifests",
                "INFO"
            )
    
    def browse_game_path(self):
        """Browse for game path"""
        path = QFileDialog.getExistingDirectory(self, "Select My Singing Monsters Game Folder")
        if path:
            # Check if it's a valid game path
            data_path = os.path.join(path, "data")
            if os.path.exists(data_path):
                self.game_path = path
                self.shader_registry.set_game_path(self.game_path)
                self.settings.setValue('game_path', path)
                self.path_label.setText(f"Game Path: {path}")
                self.log_widget.log(f"Game path set to: {path}", "SUCCESS")
                self.build_audio_library()
                self.refresh_file_list()
            else:
                QMessageBox.warning(self, "Invalid Path", 
                                  "Selected folder doesn't contain a 'data' subfolder. "
                                  "Please select the root game folder.")
                self.log_widget.log("Invalid game path selected", "ERROR")
    
    def refresh_file_list(self):
        """Refresh the list of available BIN/JSON files"""
        if not self.game_path:
            self.log_widget.log("No game path set", "WARNING")
            return
        
        xml_bin_path = os.path.join(self.game_path, "data", "xml_bin")
        if not os.path.exists(xml_bin_path):
            self.log_widget.log(f"xml_bin folder not found: {xml_bin_path}", "ERROR")
            return

        indexed_files: List[AnimationFileEntry] = []
        for root, _, files in os.walk(xml_bin_path):
            for file in files:
                lower = file.lower()
                if lower.endswith('.bin') or lower.endswith('.json'):
                    full_path = os.path.normpath(os.path.join(root, file))
                    relative_path = os.path.relpath(full_path, xml_bin_path).replace("\\", "/")
                    indexed_files.append(AnimationFileEntry(
                        name=file,
                        relative_path=relative_path,
                        full_path=full_path
                    ))

        indexed_files.sort(key=lambda entry: entry.relative_path.lower())
        self.file_index = indexed_files
        self._rebuild_monster_lookup(indexed_files)
        self.log_widget.log(f"Indexed {len(indexed_files)} BIN/JSON files", "INFO")
        self.apply_file_filter()

    def _rebuild_monster_lookup(self, entries: List[AnimationFileEntry]) -> None:
        """Build quick lookup for monster files keyed by base name."""
        lookup: Dict[str, MonsterFileRecord] = {}
        for entry in entries:
            stem = Path(entry.name).stem.lower()
            if not stem or not stem.startswith("monster_"):
                continue
            if self._is_excluded_monster_stem(stem):
                continue
            record = lookup.get(stem)
            if not record:
                record = MonsterFileRecord(stem=stem, relative_path=entry.relative_path)
                lookup[stem] = record
            if entry.is_json:
                record.json_path = entry.full_path
                record.relative_path = entry.relative_path
            elif entry.is_bin:
                if not record.bin_path:
                    record.bin_path = entry.full_path
                if not record.relative_path:
                    record.relative_path = entry.relative_path
        self.monster_file_lookup = lookup

    def _build_monster_browser_entries(self, book_dir: Path) -> List[MonsterBrowserEntry]:
        """Return MonsterBrowserEntry rows by matching portraits to indexed files."""
        if not book_dir.exists():
            return []

        entries: List[MonsterBrowserEntry] = []
        seen_tokens: Set[str] = set()
        prefix = "monster_portrait_square_"
        for path in sorted(book_dir.glob("*")):
            if path.is_dir():
                continue
            suffix = path.suffix.lower()
            if suffix not in (".png", ".jpg", ".jpeg", ".webp", ".avif"):
                continue
            stem = path.stem.lower()
            if "black" in stem:
                continue
            if not stem.startswith(prefix):
                continue
            token = stem[len(prefix):]
            if not token or token in seen_tokens:
                continue
            base_record, variant_options = self._gather_monster_variants(token)
            if not base_record or not base_record.has_source():
                continue
            json_path = base_record.json_path
            bin_path = base_record.bin_path
            display_path = base_record.relative_path or os.path.basename(json_path or bin_path or token)
            display_name = Path(json_path or bin_path or base_record.stem or token).stem
            entries.append(
                MonsterBrowserEntry(
                    token=token,
                    display_name=display_name,
                    relative_path=display_path,
                    image_path=str(path),
                    json_path=json_path,
                    bin_path=bin_path,
                    variants=variant_options,
                )
            )
            seen_tokens.add(token)

        entries.sort(key=lambda item: item.display_name.lower())
        return entries

    def _gather_monster_variants(
        self, token: str
    ) -> Tuple[Optional[MonsterFileRecord], List[MonsterVariantOption]]:
        """Return the primary record and extra variants for a monster token."""
        prefix_key = f"monster_{token}".lower()
        if not self.monster_file_lookup:
            return None, []

        primary: Optional[MonsterFileRecord] = None
        variants: List[MonsterVariantOption] = []
        for stem, record in self.monster_file_lookup.items():
            if not record.has_source():
                continue
            if stem == prefix_key:
                primary = record
                continue
            if not self._is_variant_stem(prefix_key, stem):
                continue
            label = self._format_monster_variant_label(prefix_key, stem)
            display_name = Path(
                record.json_path or record.bin_path or record.relative_path or record.stem
            ).stem
            variants.append(
                MonsterVariantOption(
                    display_name=display_name,
                    relative_path=record.relative_path,
                    json_path=record.json_path,
                    bin_path=record.bin_path,
                    variant_label=label,
                    stem=record.stem,
                )
            )
        if primary is None:
            return None, []
        variants.sort(key=lambda variant: variant.variant_label.lower())
        return primary, variants

    @staticmethod
    def _format_monster_variant_label(prefix_key: str, stem: str) -> str:
        """Return a user-friendly label for a monster variant stem."""
        if not stem.startswith(prefix_key):
            return Path(stem).stem
        suffix = stem[len(prefix_key) :].lstrip("_")
        if not suffix:
            return "Default"
        tokens = [token for token in suffix.split("_") if token]
        return " ".join(token.capitalize() for token in tokens) or "Variant"

    @staticmethod
    def _is_excluded_monster_stem(stem: str) -> bool:
        """Return True if the given monster stem corresponds to costume/track data."""
        if not stem:
            return True
        if not stem.startswith("monster_"):
            return True
        remainder = stem[len("monster_") :]
        tokens = [token for token in remainder.split("_") if token]
        forbidden = {"costume", "costumes", "track", "tracks", "rare", "epic"}
        return any(token.lower() in forbidden for token in tokens)

    @staticmethod
    def _is_variant_stem(prefix_key: str, candidate_stem: str) -> bool:
        """Return True if the candidate stem is a valid variant of the prefix stem."""
        if not candidate_stem.startswith(prefix_key):
            return False
        suffix = candidate_stem[len(prefix_key) :]
        if not suffix:
            return False  # exact match handled elsewhere
        if not suffix.startswith("_"):
            return False  # avoid collisions like monster_abd vs monster_abdn
        tokens = [token for token in suffix.split("_") if token]
        if not tokens:
            return False
        blocked = {"rare", "epic", "costume", "costumes", "track", "tracks"}
        return not any(token.lower() in blocked for token in tokens)

    def open_monster_browser(self):
        """Launch the Monster Browser dialog for visual monster selection."""
        if not self.game_path:
            QMessageBox.warning(self, "Game Path Required", "Set the game path before using the Monster Browser.")
            return

        if not self.file_index:
            self.refresh_file_list()
            if not self.file_index:
                QMessageBox.warning(self, "No Files Indexed", "Unable to index BIN/JSON files. Check your game path.")
                return

        book_dir = Path(self.game_path) / "data" / "gfx" / "book"
        if not book_dir.exists():
            fallback = self.project_root / "My Singing Monsters Game Filesystem Example" / "data" / "gfx" / "book"
            if fallback.exists():
                book_dir = fallback

        if not book_dir.exists():
            QMessageBox.warning(self, "Book Art Missing", "Could not locate data/gfx/book for portraits.")
            return

        entries = self._build_monster_browser_entries(book_dir)
        if not entries:
            QMessageBox.information(self, "No Monsters Found", "No monsters with portraits were found in the indexed files.")
            return

        stored_columns = self.settings.value('monster_browser/columns', 3, type=int)
        dialog = MonsterBrowserDialog(entries, initial_columns=max(1, int(stored_columns or 3)), parent=self)
        result = dialog.exec()
        self.settings.setValue('monster_browser/columns', dialog.column_count())
        if result == QDialog.DialogCode.Accepted and dialog.selected_entry:
            if dialog.apply_animations_to_active():
                self._apply_animations_from_entry(dialog.selected_entry, dialog.force_reexport())
            else:
                self._handle_monster_browser_selection(dialog.selected_entry, dialog.force_reexport())

    def _handle_monster_browser_selection(self, entry: MonsterBrowserEntry, force_reexport: bool):
        """Load the selected monster entry, converting BINs as needed."""
        json_path = entry.json_path if entry.json_path and os.path.exists(entry.json_path) else None
        bin_path = entry.bin_path if entry.bin_path and os.path.exists(entry.bin_path) else None

        if force_reexport or not json_path:
            if not bin_path:
                QMessageBox.warning(self, "Missing BIN", f"No BIN available to convert for {entry.display_name}.")
                return
            json_path = self._convert_bin_file(bin_path, force=True, announce=True)
            if not json_path:
                return
        elif not os.path.exists(json_path):
            self.log_widget.log(f"JSON file missing for {entry.display_name}, attempting to rebuild.", "WARNING")
            if not bin_path:
                QMessageBox.warning(self, "Missing JSON", f"JSON for {entry.display_name} not found.")
                return
            json_path = self._convert_bin_file(bin_path, force=True, announce=True)
            if not json_path:
                return

        if not self.select_file_by_path(json_path):
            self.refresh_file_list()
            self.select_file_by_path(json_path)
        self.load_json_file(json_path)

    def _apply_animations_from_entry(self, entry: MonsterBrowserEntry, force_reexport: bool):
        """Apply animations from the given entry into the currently loaded monster JSON.

        This replaces the `anims` array in the active JSON with the source's animations.
        """
        if not self.current_json_data:
            QMessageBox.warning(self, "No Active Monster", "Load a target monster first to apply animations to.")
            return

        json_path = entry.json_path if entry.json_path and os.path.exists(entry.json_path) else None
        bin_path = entry.bin_path if entry.bin_path and os.path.exists(entry.bin_path) else None

        if force_reexport or not json_path:
            if not bin_path:
                QMessageBox.warning(self, "Missing BIN", f"No BIN available to convert for {entry.display_name}.")
                return
            json_path = self._convert_bin_file(bin_path, force=True, announce=True)
            if not json_path:
                return
        elif not os.path.exists(json_path):
            self.log_widget.log(f"JSON file missing for {entry.display_name}, attempting to rebuild.", "WARNING")
            if not bin_path:
                QMessageBox.warning(self, "Missing JSON", f"JSON for {entry.display_name} not found.")
                return
            json_path = self._convert_bin_file(bin_path, force=True, announce=True)
            if not json_path:
                return

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                payload = json.load(f)
        except Exception as e:
            self.log_widget.log(f"Failed to read source JSON: {e}", "ERROR")
            QMessageBox.warning(self, "Load Failed", f"Could not load animation from {entry.display_name}: {e}")
            return

        normalized = self._normalize_animation_file_payload(payload)
        if not normalized or 'anims' not in normalized:
            QMessageBox.warning(self, "Invalid Animation", "Selected file does not contain usable animations.")
            return

        # Replace animations in current payload
        try:
            self.current_json_data['anims'] = copy.deepcopy(normalized['anims'])
            # Merge sources if present
            if 'sources' in normalized:
                existing = self.current_json_data.get('sources', []) or []
                for s in normalized.get('sources', []):
                    if s not in existing:
                        existing.append(s)
                self.current_json_data['sources'] = existing

            # Re-apply the current payload so UI updates accordingly
            current_path = self.current_json_path or ''
            self._apply_json_payload(current_path, self.current_json_data, announce=True)
            self.log_widget.log(f"Applied animations from {entry.display_name} to active monster.", "SUCCESS")
        except Exception as exc:
            self.log_widget.log(f"Failed to apply animations: {exc}", "ERROR")
            QMessageBox.warning(self, "Apply Failed", f"Failed to apply animations: {exc}")

    def on_file_search_changed(self, text: str):
        """Handle search text changes from the control panel."""
        self.current_search_text = text or ""
        self.apply_file_filter()

    def apply_file_filter(self):
        """Filter indexed files based on the search text and update the UI."""
        if not self.file_index:
            self.filtered_file_index = []
            self.update_file_combo([])
            self.control_panel.update_file_count_label(0, 0)
            return

        tokens = [token for token in self.current_search_text.lower().split() if token]
        if tokens:
            filtered = [
                entry for entry in self.file_index
                if all(token in entry.relative_path.lower() for token in tokens)
            ]
        else:
            filtered = list(self.file_index)

        self.filtered_file_index = filtered
        self.update_file_combo(filtered)
        self.control_panel.update_file_count_label(len(filtered), len(self.file_index))

        if tokens and not filtered:
            self.log_widget.log("No BIN/JSON files match the current search", "WARNING")

    def update_file_combo(self, entries: List[AnimationFileEntry]):
        """Populate the combo box with the provided file entries."""
        combo = self.control_panel.bin_combo

        previous_data = combo.currentData()
        previous_text = combo.currentText()
        previous_normalized = None
        if previous_data:
            previous_normalized = os.path.normcase(os.path.normpath(previous_data))
        elif previous_text and self.game_path:
            fallback_path = os.path.join(self.game_path, "data", "xml_bin", previous_text)
            previous_normalized = os.path.normcase(os.path.normpath(fallback_path))

        signals_blocked = combo.blockSignals(True)
        combo.clear()
        for entry in entries:
            display_text = entry.relative_path.replace("\\", "/")
            combo.addItem(display_text, entry.full_path)
        combo.blockSignals(signals_blocked)

        if previous_normalized:
            for idx, entry in enumerate(entries):
                if entry.normalized_path() == previous_normalized:
                    combo.setCurrentIndex(idx)
                    break
            else:
                if entries:
                    combo.setCurrentIndex(0)
        elif entries:
            combo.setCurrentIndex(0)

    def select_file_by_path(self, target_path: str) -> bool:
        """
        Attempt to select an entry in the combo box matching the given path.
        
        Args:
            target_path: Absolute path of the file to select
        
        Returns:
            True if the file was selected, False otherwise
        """
        normalized_target = os.path.normcase(os.path.normpath(target_path))
        combo = self.control_panel.bin_combo
        for idx, entry in enumerate(self.filtered_file_index):
            if entry.normalized_path() == normalized_target:
                combo.setCurrentIndex(idx)
                return True

        # Fallback in case filtered list is outdated relative to combo contents
        for idx in range(combo.count()):
            data_path = combo.itemData(idx)
            if not data_path:
                continue
            if os.path.normcase(os.path.normpath(data_path)) == normalized_target:
                combo.setCurrentIndex(idx)
                return True
        return False

    def _current_monster_token(self) -> Optional[str]:
        """Return the monster token derived from the active JSON filename."""
        if not self.current_json_path:
            return None
        stem = Path(self.current_json_path).stem
        if not stem:
            return None
        if stem.lower().startswith("monster_"):
            stem = stem[8:]
        return stem or None

    def _scan_costume_entries(self) -> List[CostumeEntry]:
        """Return all costume files that match the current monster token."""
        token = self._current_monster_token()
        if not token or not self.game_path:
            return []

        prefix_lower = f"costume_{token.lower()}_"
        entries: Dict[str, CostumeEntry] = {}

        xml_bin_dir = os.path.join(self.game_path, "data", "xml_bin")
        self._collect_costumes_in_dir(
            xml_bin_dir, prefix_lower, entries,
            priority=0,
            allow_bins=True, allow_json=True
        )

        extra_dirs: List[str] = []
        if self.current_json_path:
            extra_dirs.append(os.path.dirname(self.current_json_path))
        project_dir = str(self.project_root)
        if project_dir:
            extra_dirs.append(project_dir)

        priority_counter = 1
        for directory in extra_dirs:
            if not directory:
                continue
            if os.path.normcase(directory) == os.path.normcase(xml_bin_dir):
                continue
            self._collect_costumes_in_dir(
                directory, prefix_lower, entries,
                priority=priority_counter,
                allow_bins=False, allow_json=True
            )
            priority_counter += 1

        sorted_entries = [
            entry for entry in sorted(entries.values(), key=lambda e: e.key)
            if entry.source_path
        ]
        return sorted_entries

    def _refresh_costume_list(self):
        """Discover costumes for the current animation and update the UI."""
        self._invalidate_current_canonical_clones()
        entries = self._scan_costume_entries()
        self.costume_entries = entries
        self.costume_entry_map = {entry.key: entry for entry in entries}
        combo_items = [(entry.display_name, entry.key) for entry in entries]
        self.control_panel.update_costume_options(combo_items, select_index=0)
        self.control_panel.set_costume_convert_enabled(False)
        if entries:
            self.log_widget.log(f"Detected {len(entries)} costume variant(s)", "INFO")
        else:
            self.log_widget.log("No costumes detected for this monster", "INFO")

    def _get_current_costume_entry(self) -> Optional[CostumeEntry]:
        """Return the CostumeEntry for the currently selected dropdown item."""
        key = self.control_panel.costume_combo.currentData()
        if not key:
            return None
        return self.costume_entry_map.get(key)

    def _restore_costume_selection(self, key: Optional[str]):
        """Set the costume combo box back to a specific entry without firing signals."""
        combo = self.control_panel.costume_combo
        was_blocked = combo.blockSignals(True)
        if key:
            for idx in range(combo.count()):
                if combo.itemData(idx) == key:
                    combo.setCurrentIndex(idx)
                    break
            else:
                combo.setCurrentIndex(0)
        else:
            combo.setCurrentIndex(0)
        combo.blockSignals(was_blocked)
        self.control_panel.set_costume_convert_enabled(key is not None)

    def convert_selected_costume(self):
        """Convert the currently selected costume BIN file to JSON."""
        entry = self._get_current_costume_entry()
        if not entry:
            self.log_widget.log("No costume selected for conversion.", "WARNING")
            return
        if entry.json_path and os.path.exists(entry.json_path):
            rel = os.path.basename(entry.json_path)
            self.log_widget.log(f"Costume already has JSON: {rel}", "INFO")
            return
        if not entry.bin_path or not os.path.exists(entry.bin_path):
            self.log_widget.log("Selected costume has no BIN file to convert.", "ERROR")
            return

        output_path = os.path.splitext(entry.bin_path)[0] + '.json'
        if os.path.exists(output_path):
            self.log_widget.log(
                f"Target JSON already exists: {os.path.basename(output_path)}",
                "WARNING"
            )
            entry.json_path = output_path
            return

        try:
            with open(entry.bin_path, 'rb') as f:
                parsed = parse_costume_file(f.read())
            with open(output_path, 'w', encoding='utf-8') as out_f:
                json.dump(parsed, out_f, indent=2, ensure_ascii=False)
            entry.json_path = output_path
            norm_bin = os.path.normcase(os.path.normpath(entry.bin_path))
            self.costume_cache.pop(norm_bin, None)
            rel = os.path.relpath(output_path, os.path.dirname(entry.bin_path))
            self.log_widget.log(f"Converted costume BIN to JSON: {rel}", "SUCCESS")
        except Exception as exc:
            self.log_widget.log(f"Costume conversion failed: {exc}", "ERROR")
            return

        previous_key = entry.key
        self._refresh_costume_list()
        self._restore_costume_selection(previous_key)

    def _collect_costumes_in_dir(
        self,
        directory: str,
        prefix_lower: str,
        entries: Dict[str, CostumeEntry],
        priority: int,
        allow_bins: bool,
        allow_json: bool
    ):
        """Populate entries with costume files from a directory."""
        if not directory or not os.path.isdir(directory):
            return
        try:
            for name in os.listdir(directory):
                stem, ext = os.path.splitext(name)
                lower_stem = stem.lower()
                if not lower_stem.startswith(prefix_lower):
                    continue
                ext_lower = ext.lower()
                if ext_lower == '.bin' and not allow_bins:
                    continue
                if ext_lower == '.json' and not allow_json:
                    continue
                if ext_lower not in ('.json', '.bin'):
                    continue
                key = lower_stem
                entry = entries.get(key)
                if not entry:
                    entry = CostumeEntry(
                        key=key,
                        display_name=self._format_costume_display_name(stem)
                    )
                    entries[key] = entry
                full_path = os.path.join(directory, name)
                if ext_lower == '.bin':
                    if entry.bin_path is None or priority < entry.bin_priority:
                        entry.bin_path = full_path
                        entry.bin_priority = priority
                else:
                    if entry.json_path is None or priority < entry.json_priority:
                        entry.json_path = full_path
                        entry.json_priority = priority
        except FileNotFoundError:
            return
        except OSError as exc:
            self.log_widget.log(f"Failed to scan '{directory}' for costumes: {exc}", "WARNING")

    def _format_costume_display_name(self, stem: str) -> str:
        """Return a user-friendly label for a costume stem."""
        pretty = stem
        if stem.lower().startswith("costume_"):
            pretty = stem[8:]
        pretty = pretty.replace('_', ' ').strip()
        return pretty.title() if pretty else stem

    def _load_costume_definition(self, entry: CostumeEntry) -> Optional[Dict[str, Any]]:
        """Load costume data from JSON or BIN."""
        source = entry.source_path
        if not source:
            return None
        cache_key = os.path.normcase(os.path.normpath(source))
        if cache_key in self.costume_cache:
            return self.costume_cache[cache_key]
        try:
            if source.lower().endswith('.json'):
                with open(source, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            else:
                with open(source, 'rb') as f:
                    blob = f.read()
                data = parse_costume_file(blob)
            self.costume_cache[cache_key] = data
            return data
        except Exception as exc:
            self.log_widget.log(f"Failed to load costume '{entry.display_name}': {exc}", "ERROR")
            return None

    def _embedded_clone_defs(self) -> Optional[List[Dict[str, Any]]]:
        """
        Return clone metadata embedded directly in the currently loaded animation JSON.

        When the rev6 converter exports CloneData alongside the base animation it is safer
        to use those canonical records instead of inferring clone placement from costumes.
        The returned list is always a shallow copy so downstream normalization can mutate
        the entries without polluting the cached JSON data.
        """
        if self.current_animation_embedded_clones is None:
            return None
        clones: List[Dict[str, Any]] = []
        for entry in self.current_animation_embedded_clones:
            if isinstance(entry, dict):
                clones.append(dict(entry))
        return clones

    def _extract_embedded_clone_defs(
        self,
        anim_data: Optional[Dict[str, Any]]
    ) -> Optional[List[Dict[str, Any]]]:
        """Inspect the active animation JSON for baked-in clone metadata."""
        if not anim_data:
            return None

        def _coerce_clone_list(value: Any) -> Optional[List[Dict[str, Any]]]:
            if isinstance(value, list):
                return value
            return None

        direct = _coerce_clone_list(anim_data.get('clone_layers'))
        if direct is not None:
            return direct

        metadata = anim_data.get('metadata')
        meta_clones = _coerce_clone_list(metadata.get('clone_layers')) if isinstance(metadata, dict) else None
        if meta_clones is not None:
            return meta_clones

        anim_name = anim_data.get('name')
        root = self.current_json_data or {}
        if anim_name:
            per_anim = root.get('clone_layers_by_anim')
            if isinstance(per_anim, dict):
                direct_match = _coerce_clone_list(per_anim.get(anim_name))
                if direct_match is not None:
                    return direct_match
                lower_match = _coerce_clone_list(per_anim.get(anim_name.lower()))
                if lower_match is not None:
                    return lower_match

        shared = root.get('clone_layers')
        clones = _coerce_clone_list(shared)
        if clones is not None:
            return clones

        return None

    def _current_animation_clone_key(self) -> Optional[str]:
        """Return a stable cache key for canonical clone detection."""
        source = self.current_json_path or ""
        name = self.current_animation_name or ""
        if not source and not name:
            return None
        return f"{os.path.normcase(source)}|{name}"

    def _invalidate_current_canonical_clones(self):
        """Drop cached canonical clones for the active animation."""
        key = self._current_animation_clone_key()
        if key:
            self.canonical_clone_cache.pop(key, None)

    def _get_canonical_clone_defs(self) -> List[Dict[str, Any]]:
        """Return canonical clone definitions aggregated from all costumes."""
        key = self._current_animation_clone_key()
        if not key:
            return []
        embedded = self._embedded_clone_defs()
        if embedded is not None:
            self.canonical_clone_cache[key] = embedded
            return embedded
        if key in self.canonical_clone_cache:
            return self.canonical_clone_cache[key]
        clones = self._collect_canonical_clone_defs()
        self.canonical_clone_cache[key] = clones
        return clones

    def _collect_canonical_clone_defs(self) -> List[Dict[str, Any]]:
        """Gather the first clone definition for each alias across costumes."""
        entries = self._scan_costume_entries()
        canonical: Dict[str, Dict[str, Any]] = {}
        for entry in entries:
            data = self._load_costume_definition(entry)
            if not data:
                continue
            for clone in data.get('clone_layers', []):
                new_name = clone.get('new_layer') or clone.get('name')
                normalized = self._normalize_layer_label(new_name)
                if not normalized:
                    continue
                lower = normalized.lower()
                if lower in canonical:
                    continue
                canonical[lower] = clone
        return list(canonical.values())

    def _apply_canonical_clones_to_base(self, layers: List[LayerData]):
        """Insert canonical layer duplicates before any costume is applied."""
        clone_defs = self._filter_canonical_clone_defs(layers)
        if not clone_defs:
            return
        remap_map: Dict[str, Dict[str, Any]] = {}
        sheet_names: Set[str] = set()
        layer_remap_overrides: Dict[int, Dict[str, Any]] = {}
        self._apply_clone_layers(
            layers,
            clone_defs,
            remap_map,
            sheet_names,
            layer_remap_overrides,
            label="canonical"
        )
        lookup = {layer.name.lower(): layer for layer in layers}
        for entry in clone_defs:
            new_name = entry.get('new_layer') or entry.get('name')
            if not new_name:
                continue
            normalized = new_name.lower()
            target = lookup.get(normalized)
            if target:
                # Keep canonical clones hidden until a costume remaps them.
                target.visible = False
                self.diagnostics.log_canonical(
                    f"Seeded canonical clone '{new_name}' from '{entry.get('source_layer') or entry.get('resource')}'",
                    layer_id=target.layer_id,
                    extra={"reference": entry.get('reference_layer') or entry.get('sheet')}
                )
            self.canonical_layer_names.add(normalized)

    def _filter_canonical_clone_defs(self, layers: List[LayerData]) -> List[Dict[str, Any]]:
        """Return canonical clone entries that aren't already present in layers."""
        clone_defs = self._get_canonical_clone_defs()
        if not clone_defs:
            return []
        existing = {layer.name.lower() for layer in layers}
        filtered: List[Dict[str, Any]] = []
        for entry in clone_defs:
            normalized_entry = self._normalize_canonical_clone_entry(entry, existing)
            new_name = normalized_entry.get('new_layer') or normalized_entry.get('name')
            if not new_name:
                continue
            lower = new_name.lower()
            if lower in existing:
                continue
            filtered.append(normalized_entry)
            existing.add(lower)
        return filtered

    def _normalize_canonical_clone_entry(
        self,
        entry: Dict[str, Any],
        existing_names: Set[str]
    ) -> Dict[str, Any]:
        """
        Return a clone entry that uses the canonical new/source ordering.

        Early JSON exports (and the legacy parser) swapped the first two strings in the BIN,
        which made `new_layer` point at the base sprite while `source_layer` carried the alias.
        When that happens we remap the entry so canonical clone seeding uses the alias that
        does *not* exist in the base layer cache yet.
        """
        new_name = (entry.get('new_layer') or entry.get('name') or "").strip()
        source_name = (entry.get('source_layer') or entry.get('resource') or "").strip()
        if not source_name:
            return entry

        lower_new = new_name.lower()
        lower_source = source_name.lower()

        needs_swap = (
            (not new_name or lower_new in existing_names) and
            source_name and lower_source not in existing_names
        )
        if not needs_swap:
            return entry

        normalized = dict(entry)
        normalized['new_layer'] = source_name
        normalized['name'] = source_name
        normalized['source_layer'] = new_name
        normalized['resource'] = new_name
        return normalized

    def _update_canonical_clone_visibility(
        self,
        layers: List[LayerData],
        remap_map: Dict[str, Dict[str, Any]],
        layer_remap_overrides: Dict[int, Dict[str, Any]]
    ):
        """
        Mirror the runtime behavior by ensuring canonical clones only render once
        a costume remaps them onto a real resource.
        """
        if not self.canonical_layer_names:
            return
        for layer in layers:
            normalized = layer.name.lower()
            if normalized not in self.canonical_layer_names:
                continue
            remap_info = layer_remap_overrides.get(layer.layer_id) or remap_map.get(normalized)
            if not remap_info:
                layer.visible = False
                continue
            resource = (remap_info.get("resource") or "").strip().lower()
            layer.visible = resource != "empty"

    def _clone_layers(self, layers: List[LayerData]) -> List[LayerData]:
        """Deep-copy layer structures so they can be safely mutated."""
        return [
            replace(layer, keyframes=[replace(kf) for kf in layer.keyframes])
            for layer in layers
        ]

    def _duplicate_layer(
        self,
        layer: LayerData,
        *,
        new_id: int,
        new_name: str,
        anchor_layer: Optional[LayerData] = None,
        anchor_override: Optional[Tuple[float, float]] = None
    ) -> LayerData:
        """Return a deep copy of a layer with a new id/name.

        Args:
            layer: The source layer to copy keyframes and other properties from.
            new_id: The new layer ID.
            new_name: The new layer name.
            anchor_layer: Optional layer to copy anchor positions from. If None,
                         uses the source layer's anchors. CloneObjectAbove/BelowLayer
                         generates the clone entity from the source layer data, so
                         anchors should generally come from the source unless a
                         costume explicitly overrides them.
            anchor_override: Optional explicit (x, y) anchor values to use instead of
                           copying from anchor_layer or layer.
        """
        # Use explicit override if provided, otherwise anchor_layer, otherwise source layer
        if anchor_override is not None:
            anchor_x, anchor_y = anchor_override
        else:
            anchor_source = anchor_layer if anchor_layer is not None else layer
            anchor_x = anchor_source.anchor_x
            anchor_y = anchor_source.anchor_y
        return LayerData(
            name=new_name,
            layer_id=new_id,
            parent_id=layer.parent_id,
            anchor_x=anchor_x,
            anchor_y=anchor_y,
            blend_mode=layer.blend_mode,
            keyframes=[replace(kf) for kf in layer.keyframes],
            visible=layer.visible,
            shader_name=layer.shader_name,
            color_tint=copy.deepcopy(layer.color_tint),
            color_tint_hdr=copy.deepcopy(getattr(layer, "color_tint_hdr", None)),
            color_gradient=copy.deepcopy(getattr(layer, "color_gradient", None)),
            color_animator=copy.deepcopy(getattr(layer, "color_animator", None)),
            color_metadata=copy.deepcopy(getattr(layer, "color_metadata", None)),
            render_tags=set(layer.render_tags)
        )

    def _apply_clone_layers(
        self,
        layers: List[LayerData],
        clone_defs: List[Dict[str, Any]],
        remap_map: Dict[str, Dict[str, Any]],
        sheet_names: Set[str],
        layer_remap_overrides: Dict[int, Dict[str, Any]],
        *,
        label: str = "clone"
    ):
        """Insert cloned layers defined by costume metadata."""
        if not clone_defs:
            return

        def _coerce_int(value: Any) -> Optional[int]:
            if isinstance(value, int):
                return value
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    return None
                try:
                    return int(stripped, 0)
                except ValueError:
                    return None
            return None

        def _signed_variant(value: Any) -> Optional[int]:
            coerced = _coerce_int(value)
            if coerced is None:
                return None
            coerced &= 0xFFFFFFFF
            return coerced if coerced < 0x80000000 else coerced - 0x100000000

        def _resolve_insert_mode(entry: Dict[str, Any]) -> Tuple[int, Optional[int]]:
            variant_signed = _signed_variant(entry.get('variant_index'))
            insert_raw = _coerce_int(entry.get('insert_mode'))
            if insert_raw is None:
                insert_raw = variant_signed if variant_signed is not None else 1
            resolved_mode = -1 if insert_raw < 0 else 1
            entry['_resolved_variant_index'] = variant_signed
            entry['_resolved_insert_mode'] = resolved_mode
            return resolved_mode, variant_signed

        layer_lookup = {layer.name.lower(): layer for layer in layers}
        next_id = max((layer.layer_id for layer in layers), default=-1)

        for entry in clone_defs:
            new_name = entry.get('new_layer') or entry.get('name')
            source_name = entry.get('source_layer') or entry.get('resource')
            reference_name = entry.get('reference_layer') or entry.get('sheet')
            insert_mode, variant_signed = _resolve_insert_mode(entry)

            if not new_name or not source_name:
                continue

            normalized_new = (new_name or "").strip().lower()
            normalized_source = (source_name or "").strip().lower()
            source_exact_exists = bool(normalized_source and normalized_source in layer_lookup)
            new_exact_exists = bool(normalized_new and normalized_new in layer_lookup)
            canonical_aliases = self.canonical_layer_names or set()
            source_is_canonical = normalized_source in canonical_aliases
            new_is_canonical = normalized_new in canonical_aliases

            should_swap = (
                label != "canonical"
                and new_name and source_name
                and (
                    (not source_exact_exists and new_exact_exists)
                    or (source_is_canonical and not new_is_canonical)
                )
            )
            if should_swap:
                source_name, new_name = new_name, source_name
                normalized_new, normalized_source = normalized_source, normalized_new
                entry['new_layer'] = new_name
                entry['name'] = new_name
                entry['source_layer'] = source_name
                entry['resource'] = source_name
                self.diagnostics.log_clone(
                    f"{label.title()} swapped legacy clone entry to '{new_name}' from '{source_name}'",
                    severity="DEBUG"
                )

            alias_exists = bool(normalized_new and normalized_new in layer_lookup)
            source_candidates = self._layer_name_variants(
                source_name,
                new_name,
                reference_name
            )
            source_layer = self._find_layer_in_lookup(layer_lookup, source_candidates)

            if not source_layer and source_name and new_name:
                # Legacy exports swapped new/source fields. Retry with swapped labels.
                legacy_candidates = self._layer_name_variants(
                    new_name,
                    source_name,
                    reference_name
                )
                source_layer = self._find_layer_in_lookup(layer_lookup, legacy_candidates)
                if source_layer:
                    source_name, new_name = new_name, source_name
                    entry['new_layer'] = new_name
                    entry['name'] = new_name
                    entry['source_layer'] = source_name
                    entry['resource'] = source_name

            if alias_exists and normalized_new in self.canonical_layer_names:
                self.diagnostics.log_clone(
                    f"{label.title()} skipped '{new_name}' because canonical clone already exists",
                    layer_id=layer_lookup[normalized_new].layer_id if normalized_new in layer_lookup else None,
                    severity="DEBUG"
                )
                # Already seeded from canonical clones.
                continue

            if not source_layer:
                self.diagnostics.log_clone(
                    f"{label.title()} source layer missing for '{new_name}' (source: {source_name})",
                    severity="WARNING"
                )
                self.log_widget.log(
                    f"{label.title()} source layer missing for '{new_name}' "
                    f"(source: {source_name})",
                    "WARNING"
                )
                continue

            reference_candidates = self._layer_name_variants(reference_name)
            reference_layer = None
            if reference_candidates:
                reference_layer = self._find_layer_in_lookup(layer_lookup, reference_candidates)
            if not reference_layer:
                self.diagnostics.log_clone(
                    f"{label.title()} reference layer missing for '{new_name}' (reference: {reference_name})",
                    severity="WARNING"
                )
                self.log_widget.log(
                    f"{label.title()} reference layer missing for '{new_name}' "
                    f"(reference: {reference_name})",
                    "WARNING"
                )
                continue

            entry['_resolved_reference_layer'] = reference_layer.name
            next_id += 1
            # Clone uses the source layer's anchor. The runtime's clone helper builds a
            # fresh entity from the source layer data, then inserts it above/below the
            # reference. Our insert ordering still honors the resolved reference layer.
            new_layer = self._duplicate_layer(
                source_layer,
                new_id=next_id,
                new_name=new_name,
                anchor_layer=source_layer
            )

            order_reference = self._resolve_overlay_reference(reference_layer, layer_lookup)
            ref_index = layers.index(order_reference or reference_layer)
            insert_idx = ref_index
            if insert_mode is not None:
                if insert_mode > 0:
                    insert_idx = max(0, ref_index)
                else:
                    insert_idx = min(len(layers), ref_index + 1)
            layers.insert(insert_idx, new_layer)
            normalized_name = new_name.lower()
            name_conflict = normalized_name in layer_lookup
            layer_lookup[normalized_name] = new_layer
            overlay_anchor = (
                order_reference
                if order_reference and order_reference is not reference_layer
                else None
            )
            force_opaque = False
            if overlay_anchor and overlay_anchor.name:
                new_layer.render_tags.add(f"overlay_ref:{overlay_anchor.name.lower()}")
                if reference_layer and reference_layer.name:
                    new_layer.render_tags.add(f"overlay_ref_source:{reference_layer.name.lower()}")

            # Costumes often clone shade/mask layers (low-opacity or tinted) to create opaque overlays.
            source_label = (source_layer.name or source_name or "").lower() if source_layer else ""
            new_label = (new_name or "").lower()
            shade_keywords = (" shade", "shadow", " mask")
            source_is_shade = any(keyword in source_label for keyword in shade_keywords)
            new_is_shade = any(keyword in new_label for keyword in shade_keywords)
            if source_is_shade and not new_is_shade:
                force_opaque = True

            # Allow remap lookups so costume sprites are applied to the clone.
            if alias_exists:
                remap_candidates = self._layer_name_variants(
                    source_name,
                    reference_name
                )
            else:
                remap_candidates = self._layer_name_variants(
                    new_name,
                    source_name,
                    reference_name
                )
            remap_info = self._alias_remap_entry(
                remap_map,
                new_name,
                *remap_candidates,
                update_map=not name_conflict
            )
            if remap_info:
                layer_remap_overrides[new_layer.layer_id] = remap_info
                if source_layer:
                    source_layer.render_tags.add("neutral_color")
                if self._remap_targets_costume_sheet(remap_info):
                    base_opacity = self._layer_default_opacity(source_layer)
                    if base_opacity is not None and base_opacity < 99.5:
                        force_opaque = True

            if force_opaque:
                new_layer.render_tags.add("overlay_force_opaque")

            if reference_name and reference_name.lower().endswith('.xml'):
                sheet_names.add(reference_name)

            self.diagnostics.log_clone(
                f"{label.title()} inserted '{new_name}' from '{source_name}' near '{reference_layer.name}'",
                layer_id=new_layer.layer_id,
                extra={
                    "reference": reference_layer.name,
                    "mode": "above" if (insert_mode and insert_mode > 0) else "below",
                    "variant": variant_signed
                }
            )

    def _alias_remap_entry(
        self,
        remap_map: Dict[str, Dict[str, Any]],
        alias_name: Optional[str],
        *candidates: Optional[str],
        update_map: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Map alias_name to the same remap info as the first candidate found."""
        if not alias_name:
            return None
        alias_key = alias_name.lower()
        for candidate in candidates:
            if not candidate:
                continue
            info = remap_map.get(candidate.lower())
            if info:
                if update_map and alias_key not in remap_map:
                    remap_map[alias_key] = info
                return info
        return None

    def _find_layer_in_lookup(
        self,
        lookup: Dict[str, LayerData],
        candidates: Tuple[Optional[str], ...]
    ) -> Optional[LayerData]:
        """Return first matching layer from a list of candidate names."""
        for candidate in candidates:
            if candidate and candidate.lower() in lookup:
                return lookup[candidate.lower()]
        return None

    def _normalize_layer_label(self, name: Optional[str]) -> Optional[str]:
        """Return a normalized layer label with trimmed whitespace and suffixes removed."""
        if not name:
            return None
        normalized = name.strip()
        if not normalized:
            return None
        normalized = re.sub(r"\s+", " ", normalized)
        simplified = re.sub(r"\s*\(\d+\)$", "", normalized).strip()
        return simplified or normalized

    def _layer_name_variants(self, *names: Optional[str]) -> Tuple[Optional[str], ...]:
        """Return candidate layer names including normalized fallbacks."""
        variants: List[Optional[str]] = []
        seen: Set[str] = set()
        for entry in names:
            for candidate in (entry, self._normalize_layer_label(entry)):
                if not candidate:
                    continue
                lower = candidate.lower()
                if lower in seen:
                    continue
                seen.add(lower)
                variants.append(candidate)
        return tuple(variants)

    def _apply_shader_overrides(
        self,
        layers: List[LayerData],
        shader_defs: List[Dict[str, str]]
    ):
        """Attach shader metadata to layers."""
        if not shader_defs:
            return

        lookup = {layer.name.lower(): layer for layer in layers}
        for shader in shader_defs:
            node = shader.get('node')
            shader_name = shader.get('resource')
            if not node or not shader_name:
                continue
            layer = self._match_layer_by_name(lookup, node)
            if not layer:
                self.log_widget.log(
                    f"Shader target not found: {node}",
                    "WARNING"
                )
                continue
            layer.shader_name = shader_name

    def _match_layer_by_name(
        self,
        lookup: Dict[str, LayerData],
        node_name: str
    ) -> Optional[LayerData]:
        """Return the best matching layer for a shader node string."""
        normalized = node_name.lower()
        if normalized in lookup:
            return lookup[normalized]

        stripped = normalized
        if '.' in stripped:
            stripped = stripped.rsplit('.', 1)[0]
        if stripped in lookup:
            return lookup[stripped]

        stripped = re.sub(r'\s+\d+$', '', stripped)
        return lookup.get(stripped)

    def _reset_costume_runtime_state(
        self,
        layers: Optional[List[LayerData]] = None
    ) -> None:
        """Clear costume attachment and alias state between animation loads."""
        animation_layers = layers
        if animation_layers is None:
            animation = getattr(self.gl_widget.player, "animation", None)
            animation_layers = animation.layers if animation else []
        self.active_costume_key = None
        self.active_costume_attachments = []
        self.costume_sheet_aliases.clear()
        self.gl_widget.set_costume_attachments([], animation_layers or [])

    def _apply_costume_to_animation(self, entry: Optional[CostumeEntry]):
        """Apply or remove a costume by rebuilding layer data and texture atlases."""
        animation = self.gl_widget.player.animation
        if not animation or not self.base_layer_cache:
            return

        if entry is None:
            animation.layers = self._clone_layers(self.base_layer_cache)
            self.gl_widget.texture_atlases = list(self.base_texture_atlases)
            self.gl_widget.set_layer_atlas_overrides({})
            self.gl_widget.set_layer_pivot_context({})
            self._reset_costume_runtime_state(animation.layers)
            self._configure_costume_shaders(None, None)
            self.gl_widget.update()
            self.update_layer_panel()
            self._capture_pose_baseline()
            self._reset_edit_history()
            self._refresh_timeline_keyframes()
            return

        costume_data = self._load_costume_definition(entry)
        if not costume_data:
            return

        attachments = costume_data.get('ae_anim_layers', [])
        self.active_costume_attachments = self._prepare_costume_attachments(attachments)
        attachment_payloads = self._build_attachment_payloads(self.active_costume_attachments)
        if attachments and not attachment_payloads:
            self.log_widget.log(
                f"Costume defines {len(attachments)} attachment(s) but none could be loaded.",
                "WARNING"
            )

        layers = self._clone_layers(self.base_layer_cache)
        remap_map, sheet_names = self._build_remap_map(costume_data.get('remaps', []))
        sheet_alias, alias_targets = self._normalize_sheet_remaps(
            costume_data.get('sheet_remaps') or costume_data.get('swaps', [])
        )
        layer_remap_overrides: Dict[int, Dict[str, Any]] = {}
        self.costume_sheet_aliases = sheet_alias
        sheet_names.update(alias_targets)
        self._apply_clone_layers(
            layers,
            costume_data.get('clone_layers', []),
            remap_map,
            sheet_names,
            layer_remap_overrides,
            label="clone"
        )
        self._apply_remaps_to_layers(layers, remap_map, layer_remap_overrides)
        self._update_canonical_clone_visibility(layers, remap_map, layer_remap_overrides)
        self._apply_shader_overrides(layers, costume_data.get('apply_shader', []))
        self._apply_blend_overrides(layers, costume_data.get('set_blend_layers', []))
        self._normalize_costume_layer_blends(layers, remap_map, layer_remap_overrides)
        layer_color_data = (
            costume_data.get('layer_colors')
            or costume_data.get('layer_color_overrides')
        )
        self._apply_layer_color_overrides(layers, layer_color_data)
        self._enforce_costume_overlay_order(layers)
        self._assign_attachment_targets(attachment_payloads, layers)

        costume_atlases = self._load_costume_atlases(sheet_names)
        base_atlases = self._apply_sheet_aliases_to_base_atlases(self.base_texture_atlases, sheet_alias)
        combined_atlases: List[TextureAtlas] = []
        for atlas in costume_atlases:
            if atlas not in combined_atlases:
                combined_atlases.append(atlas)
        for atlas in base_atlases:
            if atlas not in combined_atlases:
                combined_atlases.append(atlas)
        self.gl_widget.texture_atlases = combined_atlases

        animation.layers = layers
        self._record_layer_defaults(animation.layers)
        self.active_costume_key = entry.key
        overrides, pivot_context = self._build_layer_atlas_overrides(
            layers,
            remap_map,
            layer_remap_overrides,
            costume_atlases,
            sheet_alias,
        )
        self.gl_widget.set_layer_atlas_overrides(overrides)
        self.gl_widget.set_layer_pivot_context(pivot_context)
        self._configure_costume_shaders(entry, costume_data)
        self.gl_widget.set_costume_attachments(attachment_payloads, layers)
        self.gl_widget.update()
        self.update_layer_panel()
        self.gl_widget.set_anchor_logging_enabled(self.anchor_debug_enabled)
        if self.anchor_debug_enabled:
            QTimer.singleShot(500, lambda: self._dump_anchor_debug())
        self._capture_pose_baseline()
        self._reset_edit_history()
        self._refresh_timeline_keyframes()

    def _build_remap_map(
        self, remaps: List[Dict[str, Any]]
    ) -> Tuple[Dict[str, Dict[str, Any]], Set[str]]:
        """Return per-layer remap information and the atlas sheets it needs."""
        remap_dict: Dict[str, Dict[str, Any]] = {}
        sheets: Set[str] = set()
        for entry in remaps or []:
            name = entry.get('display_name')
            if not name:
                continue
            frame_mappings = entry.get('frame_mappings', [])
            frame_exact: Dict[str, str] = {}
            frame_lower: Dict[str, str] = {}
            for mapping in frame_mappings:
                src = mapping.get('from')
                dst = mapping.get('to')
                if not src or not dst:
                    continue
                frame_exact[src] = dst
                frame_lower[src.lower()] = dst
            remap_info = {
                'display_name': entry.get('display_name', ''),
                'resource': entry.get('resource', ''),
                'frame_exact': frame_exact,
                'frame_lower': frame_lower,
                'sheet': entry.get('sheet', '')
            }
            remap_dict[name.lower()] = remap_info
            sheet_name = entry.get('sheet')
            if sheet_name:
                sheets.add(sheet_name)
        return remap_dict, sheets

    def _build_layer_atlas_overrides(
        self,
        layers: List[LayerData],
        remap_map: Dict[str, Dict[str, Any]],
        layer_remap_overrides: Dict[int, Dict[str, Any]],
        costume_atlases: List[TextureAtlas],
        sheet_aliases: Dict[str, List[str]]
    ) -> Tuple[Dict[int, List[TextureAtlas]], Dict[int, bool]]:
        """Map layer ids to the costume atlases they should search first."""
        overrides: Dict[int, List[TextureAtlas]] = {}
        pivot_context: Dict[int, bool] = {}
        if (not remap_map and not layer_remap_overrides) or not costume_atlases:
            return overrides, pivot_context
        atlas_lookup: Dict[str, List[TextureAtlas]] = {}
        for atlas in costume_atlases:
            keys = self._canonical_sheet_keys(getattr(atlas, "source_name", None) or atlas.image_path)
            for key in keys:
                atlas_lookup.setdefault(key, []).append(atlas)
        def _has_valid_sheet(value: Optional[str]) -> bool:
            if not value:
                return False
            normalized = value.strip().lower()
            if not normalized:
                return False
            return normalized not in {"empty", "empty.xml"}

        def _has_sprite_resource(info: Optional[Dict[str, Any]]) -> bool:
            if not info:
                return False
            resource = (info.get('resource') or '').strip().lower()
            if not resource:
                return False
            return resource not in {"empty"}

        for layer in layers:
            info = layer_remap_overrides.get(layer.layer_id)
            if not info:
                info = remap_map.get(layer.name.lower())
            sheet = (info or {}).get('sheet')
            if _has_valid_sheet(sheet) and _has_sprite_resource(info):
                pivot_context[layer.layer_id] = True
            keys = self._canonical_sheet_keys(sheet)
            matched = self._resolve_atlases_for_keys(keys, atlas_lookup)
            if not matched and keys:
                alias_targets: Set[str] = set()
                for key in keys:
                    for alias_target in sheet_aliases.get(key, []):
                        alias_targets.add(alias_target)
                alias_keys: Set[str] = set()
                for alias_name in alias_targets:
                    alias_keys.update(self._canonical_sheet_keys(alias_name))
                matched = self._resolve_atlases_for_keys(alias_keys, atlas_lookup)
            if matched:
                overrides[layer.layer_id] = matched
        return overrides, pivot_context

    def _resolve_atlases_for_keys(
        self,
        keys: Set[str],
        atlas_lookup: Dict[str, List[TextureAtlas]]
    ) -> Optional[List[TextureAtlas]]:
        for key in keys:
            bucket = atlas_lookup.get(key)
            if bucket:
                return bucket
        return None

    def _canonical_sheet_keys(self, sheet: Optional[str]) -> Set[str]:
        """Return normalized identifiers for a sheet path."""
        keys: Set[str] = set()
        if not sheet:
            return keys
        normalized = sheet.replace("\\", "/").strip()
        lowered = normalized.lower()
        if lowered:
            keys.add(lowered)
        try:
            path = Path(normalized)
        except Exception:
            path = Path(normalized.replace(":", "", 1))
        name = path.name.lower()
        if name:
            keys.add(name)
            stem = Path(name).stem.lower()
            if stem:
                keys.add(stem)
            base = self._sheet_base_name(name)
            if base:
                keys.add(base.lower())
        parent = path.parent
        if parent and parent.name:
            parent_name = parent.name.lower()
            if parent_name:
                if name:
                    keys.add(f"{parent_name}/{name}")
                stem = Path(name).stem.lower() if name else ""
                if stem:
                    keys.add(f"{parent_name}/{stem}")
        if ":" in lowered:
            suffix = lowered.split(":", 1)[1].lstrip("/")
            if suffix:
                keys.add(suffix)
        return {key for key in keys if key}

    def _normalize_sheet_remaps(
        self, remaps: List[Dict[str, str]]
    ) -> Tuple[Dict[str, List[str]], Set[str]]:
        """Normalize sheet remap entries into alias maps and target sheet names."""
        alias: Dict[str, List[str]] = {}
        targets: Set[str] = set()
        for entry in remaps or []:
            source = entry.get('from')
            target = entry.get('to')
            if not source or not target:
                continue
            for source_key in self._canonical_sheet_keys(source):
                bucket = alias.setdefault(source_key, [])
                if target not in bucket:
                    bucket.append(target)
            targets.add(target)
        return alias, targets

    def _apply_remaps_to_layers(
        self,
        layers: List[LayerData],
        remap_map: Dict[str, Dict[str, Any]],
        layer_remap_overrides: Dict[int, Dict[str, Any]]
    ):
        """Mutate keyframes according to per-layer remap definitions."""
        for layer in layers:
            render_tags = getattr(layer, "render_tags", set())
            force_full_opacity = (
                isinstance(render_tags, set)
                and "overlay_force_opaque" in render_tags
            )
            remap_info = layer_remap_overrides.get(layer.layer_id)
            if not remap_info:
                remap_info = remap_map.get(layer.name.lower())
            if not remap_info:
                continue
            has_custom_color = self._layer_has_costume_color(layer)
            if not has_custom_color:
                render_tags.add("neutral_color")
            for keyframe in layer.keyframes:
                sprite_name = keyframe.sprite_name or ""
                remapped = self._remap_sprite(sprite_name, remap_info)
                changed = remapped != sprite_name
                if changed:
                    keyframe.sprite_name = remapped
                if not has_custom_color and (changed or force_full_opacity):
                    self._neutralize_keyframe_color(keyframe)
                if force_full_opacity and (changed or remap_info.get("resource")):
                    self._force_keyframe_opacity(keyframe)

    def _remap_sprite(self, sprite_name: str, remap_info: Dict[str, Any]) -> str:
        """Return a sprite name after applying frame-based remaps."""
        if not sprite_name:
            return remap_info.get('resource') or sprite_name
        mapping = remap_info.get('frame_exact', {})
        lowered = remap_info.get('frame_lower', {})
        new_name = mapping.get(sprite_name)
        if new_name is None:
            new_name = lowered.get(sprite_name.lower())
        if new_name is None:
            fallback = remap_info.get('resource')
            return fallback if fallback else sprite_name
        return new_name

    @staticmethod
    def _neutralize_keyframe_color(keyframe: KeyframeData) -> None:
        """Reset RGB multipliers so costume sprites render with authored colours."""
        keyframe.r = 255
        keyframe.g = 255
        keyframe.b = 255
        if keyframe.immediate_rgb is None or keyframe.immediate_rgb < 0:
            keyframe.immediate_rgb = 0

    @staticmethod
    def _force_keyframe_opacity(
        keyframe: KeyframeData,
        value: float = 100.0
    ) -> None:
        """Clamp opacity to an explicit value so overlays stay opaque."""
        keyframe.opacity = value
        if keyframe.immediate_opacity is None or keyframe.immediate_opacity < 0:
            keyframe.immediate_opacity = 0

    def _apply_blend_overrides(
        self, layers: List[LayerData], overrides: List[Dict[str, Any]]
    ):
        """Update blend modes specified by the costume definition."""
        if not overrides:
            return
        lookup = {layer.name.lower(): layer for layer in layers}
        for override in overrides:
            layer_name = override.get('name')
            if not layer_name:
                continue
            layer = lookup.get(layer_name.lower())
            if not layer:
                continue
            try:
                raw_value = int(override.get('blend_value', layer.blend_mode))
            except (TypeError, ValueError):
                continue
            layer.blend_mode = self._normalize_blend_value(raw_value, self.current_blend_version or 1)

    def _apply_layer_color_overrides(
        self,
        layers: List[LayerData],
        overrides: Optional[List[Dict[str, Any]]]
    ):
        """Attach per-layer color tint overrides emitted by the costume parser."""
        if not overrides:
            return
        lookup = {layer.name.lower(): layer for layer in layers}
        for entry in overrides:
            layer_name = entry.get("layer") or entry.get("name")
            if not layer_name:
                continue
            layer = self._match_layer_by_name(lookup, layer_name)
            if not layer:
                continue
            profile = self._build_layer_color_profile(entry)
            if not profile:
                continue
            gradient_info = self._build_gradient_definition(entry)
            animation_info = self._build_color_animation_definition(entry)

            base_tint = profile.get("srgb")
            hdr_tint = profile.get("hdr")

            has_dynamic = bool(gradient_info or animation_info)
            if base_tint:
                if not has_dynamic and self._color_tuple_is_identity(base_tint):
                    layer.color_tint = None
                else:
                    layer.color_tint = base_tint
            if hdr_tint:
                layer.color_tint_hdr = hdr_tint

            if gradient_info:
                layer.color_gradient = gradient_info
            if animation_info:
                layer.color_animator = animation_info

            metadata = dict(entry)
            metadata["_color_profile"] = profile
            layer.color_metadata = metadata

    def _build_layer_color_profile(self, entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Return both SDR and HDR-friendly tuples for a layer color entry."""
        rgba8 = entry.get("rgba") or {}
        rgba16 = entry.get("rgba16") or {}
        srgb: List[float] = []
        hdr: List[float] = []

        def _extract_value(payload: Dict[str, Any], key: str) -> Optional[float]:
            value = payload.get(key)
            if value is None:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        for channel in ("r", "g", "b", "a"):
            eight_bit = _extract_value(rgba8, channel)
            sixteen_bit = _extract_value(rgba16, channel)

            # SDR color prioritizes 8-bit data, then falls back to 16-bit precision.
            source = eight_bit if eight_bit is not None else sixteen_bit
            scale = 255.0 if eight_bit is not None else 65535.0
            if source is None or scale <= 0:
                srgb.append(1.0)
            else:
                srgb.append(max(0.0, min(scale, source)) / scale)

            # HDR preview prioritizes 16-bit data to preserve range/precision.
            hdr_source = sixteen_bit if sixteen_bit is not None else eight_bit
            hdr_scale = 65535.0 if sixteen_bit is not None else 255.0
            if hdr_source is None or hdr_scale <= 0:
                hdr.append(1.0)
            else:
                hdr.append(max(0.0, hdr_source) / hdr_scale)

        profile = {
            "srgb": tuple(srgb),
            "hdr": tuple(hdr),
            "rgba": rgba8,
            "rgba16": rgba16,
            "hex": entry.get("hex"),
        }
        return profile

    def _color_tuple_is_identity(self, tint: Tuple[float, float, float, float]) -> bool:
        return all(abs(component - 1.0) < 1e-4 for component in tint)

    def _layer_has_costume_color(self, layer: LayerData) -> bool:
        """Return True if the costume authored a tint/gradient for this layer."""
        if getattr(layer, "color_gradient", None) or getattr(layer, "color_animator", None):
            return True
        metadata = getattr(layer, "color_metadata", None)
        if not isinstance(metadata, dict):
            return False
        profile = metadata.get("_color_profile")
        if not isinstance(profile, dict):
            return False
        srgb = profile.get("srgb")
        if isinstance(srgb, (tuple, list)) and len(srgb) == 4:
            if not self._color_tuple_is_identity(tuple(srgb)):
                return True
        return False

    @staticmethod
    def _layer_default_opacity(layer: Optional[LayerData]) -> Optional[float]:
        """Return the first authored opacity for a layer, if available."""
        if not layer or not layer.keyframes:
            return None
        for keyframe in layer.keyframes:
            if keyframe.immediate_opacity != -1:
                return keyframe.opacity
        return layer.keyframes[0].opacity

    def _resolve_overlay_reference(
        self,
        reference_layer: LayerData,
        layer_lookup: Dict[str, LayerData]
    ) -> Optional[LayerData]:
        """
        Return an alternate ordering anchor when the reference layer is a shade/mask.
        Costume overlays often reference the shading layer to inherit transforms but
        should render above the primary sprite (e.g., apron over body shade). Strip
        known suffixes to locate the base layer when available.
        """
        name = (reference_layer.name or "").strip()
        if not name:
            return None
        lowered = name.lower()
        for suffix in (" shade", " shadow", " mask"):
            if lowered.endswith(suffix):
                candidate_name = name[: -len(suffix)].strip()
                if candidate_name:
                    candidate = layer_lookup.get(candidate_name.lower())
                    if candidate:
                        return candidate
        return None

    @staticmethod
    def _remap_targets_costume_sheet(remap_info: Dict[str, Any]) -> bool:
        """Detect if the remap swaps sprites using a dedicated costume atlas."""
        sheet = (remap_info.get("sheet") or "").strip().lower()
        if not sheet:
            return False
        if "costume" in sheet:
            return True
        # Normalize to basename if path-like
        if "/" in sheet:
            sheet = sheet.rsplit("/", 1)[-1]
        return "costume" in sheet

    @staticmethod
    def _overlay_anchor_name(layer: LayerData) -> Optional[str]:
        """Return the overlay anchor stored in render tags, if any."""
        for tag in getattr(layer, "render_tags", set()):
            if tag.startswith("overlay_ref:"):
                anchor = tag.split(":", 1)[1].strip().lower()
                if anchor:
                    return anchor
        return None

    @staticmethod
    def _overlay_reference_name(layer: LayerData) -> Optional[str]:
        """Return the shading/mask reference stored in render tags, if any."""
        for tag in getattr(layer, "render_tags", set()):
            if tag.startswith("overlay_ref_source:"):
                ref = tag.split(":", 1)[1].strip().lower()
                if ref:
                    return ref
        return None

    def _enforce_costume_overlay_order(self, layers: List[LayerData]) -> None:
        """Ensure costume overlays render in front of their base sprites."""
        if not layers:
            return
        name_lookup = {
            (layer.name or "").strip().lower(): layer
            for layer in layers
            if layer.name
        }
        for layer in list(layers):
            anchor_name = self._overlay_anchor_name(layer)
            reference_name = self._overlay_reference_name(layer)
            if not anchor_name and not reference_name:
                continue
            anchor = name_lookup.get(anchor_name) if anchor_name else None
            reference = name_lookup.get(reference_name) if reference_name else None
            # Determine the earliest index we must precede.
            candidate_indices: List[int] = []
            if anchor and anchor is not layer:
                candidate_indices.append(layers.index(anchor))
            if reference and reference is not layer:
                candidate_indices.append(layers.index(reference))
            if not candidate_indices:
                continue
            target_index = min(candidate_indices)
            current_index = layers.index(layer)
            if current_index < target_index:
                continue
            # Move overlay directly before the earliest dependency so it draws last
            # once the renderer reverses the layer list.
            layers.insert(target_index, layers.pop(current_index))

    def _normalize_costume_layer_blends(
        self,
        layers: List[LayerData],
        remap_map: Dict[str, Dict[str, Any]],
        layer_remap_overrides: Dict[int, Dict[str, Any]]
    ) -> None:
        """Force costume-remapped layers back to Standard blend unless explicitly authored."""
        for layer in layers:
            remap_info = layer_remap_overrides.get(layer.layer_id)
            if not remap_info:
                remap_info = remap_map.get(layer.name.lower())
            if not remap_info:
                continue
            if self._remap_targets_costume_sheet(remap_info):
                layer.blend_mode = BlendMode.STANDARD

    def _build_gradient_definition(self, entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Return a normalized gradient definition if the entry specifies one."""
        gradient_source = self._extract_gradient_source(entry)
        if not gradient_source:
            return None
        stops = self._normalize_color_stops(gradient_source)
        if len(stops) < 2:
            return None
        info = {
            "stops": stops,
            "mode": (entry.get("gradient_mode") or entry.get("mode") or "loop").lower(),
            "loop": entry.get("loop"),
            "period": self._coerce_float(
                entry.get("gradient_period")
                or entry.get("period")
                or entry.get("duration")
            ),
            "offset": self._coerce_float(
                entry.get("gradient_offset") or entry.get("start_time") or entry.get("offset")
            ) or 0.0,
            "speed": self._coerce_float(
                entry.get("gradient_speed") or entry.get("speed") or entry.get("tempo_multiplier")
            ) or 1.0,
            "ping_pong": bool(entry.get("ping_pong")),
            "metadata": entry,
        }
        return info

    def _extract_gradient_source(self, entry: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """Return a list of gradient stops if one is defined."""
        candidates = [
            entry.get("gradient"),
            entry.get("gradient_stops"),
            entry.get("color_ramp"),
            entry.get("ramp"),
        ]
        for candidate in candidates:
            if isinstance(candidate, dict):
                nested = candidate.get("stops") or candidate.get("colors")
                if isinstance(nested, list):
                    return nested
            elif isinstance(candidate, list):
                return candidate
        if entry.get("mode") in {"gradient", "ramp"} and isinstance(entry.get("stops"), list):
            return entry.get("stops")
        return None

    def _normalize_color_stops(self, stop_entries: List[Any]) -> List[Dict[str, Any]]:
        """Normalize gradient stop payloads into sorted tuples."""
        stops: List[Dict[str, Any]] = []
        max_time: Optional[float] = None
        for idx, stop in enumerate(stop_entries):
            if not isinstance(stop, dict):
                continue
            profile = self._build_layer_color_profile(stop)
            if not profile:
                continue
            position = self._coerce_float(
                stop.get("position")
                or stop.get("offset")
                or stop.get("t")
                or stop.get("percent")
            )
            absolute_time = self._coerce_float(stop.get("time") or stop.get("frame"))
            if position is None and absolute_time is None:
                if len(stop_entries) > 1:
                    position = idx / float(len(stop_entries) - 1)
                else:
                    position = 0.0
            if absolute_time is not None:
                max_time = max(max_time or absolute_time, absolute_time)
            stops.append(
                {
                    "position": position,
                    "time": absolute_time,
                    "color": profile["srgb"],
                    "hdr": profile["hdr"],
                    "hex": profile.get("hex"),
                    "interpolation": stop.get("interpolation") or stop.get("mode"),
                    "source": stop,
                }
            )
        if not stops:
            return []
        if all(stop["position"] is None for stop in stops):
            if max_time and max_time > 0:
                for stop in stops:
                    if stop["time"] is not None:
                        stop["position"] = max(0.0, min(1.0, stop["time"] / max_time))
            if all(stop["position"] is None for stop in stops):
                for idx, stop in enumerate(stops):
                    stop["position"] = idx / float(max(1, len(stops) - 1))
        stops.sort(key=lambda item: item["position"])
        return stops

    def _build_color_animation_definition(self, entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Return a normalized color animation curve if one is defined."""
        frames, frame_source = self._extract_animation_frames(entry)
        if not frames:
            return None
        keyframes = self._normalize_color_keyframes(frames)
        if len(keyframes) < 2:
            return None
        duration = self._coerce_float(
            entry.get("animation_duration")
            or (frame_source.get("duration") if isinstance(frame_source, dict) else None)
            or entry.get("duration")
        )
        if not duration:
            duration = keyframes[-1]["time"]
        if duration is None or duration <= 0:
            duration = max(keyframes[-1]["time"], 0.001)
        info = {
            "keyframes": keyframes,
            "duration": duration,
            "loop": entry.get("loop", True),
            "mode": (entry.get("animation_mode") or entry.get("mode") or "loop").lower(),
            "offset": self._coerce_float(entry.get("start_time") or entry.get("offset")) or 0.0,
            "speed": self._coerce_float(
                entry.get("animation_speed") or entry.get("speed") or entry.get("tempo_multiplier")
            ) or 1.0,
            "metadata": entry,
        }
        return info

    def _extract_animation_frames(
        self, entry: Dict[str, Any]
    ) -> Tuple[Optional[List[Any]], Optional[Dict[str, Any]]]:
        """Return a timeline list from various schema permutations."""
        for key in ("animation", "timeline", "keyframes", "frames"):
            payload = entry.get(key)
            if isinstance(payload, dict):
                seq = payload.get("keyframes") or payload.get("frames")
                if isinstance(seq, list):
                    return seq, payload
            elif isinstance(payload, list):
                return payload, entry
        animated = entry.get("animated") or entry.get("anim")
        if isinstance(animated, dict):
            seq = animated.get("keyframes") or animated.get("frames")
            if isinstance(seq, list):
                return seq, animated
        return None, None

    def _normalize_color_keyframes(self, frames: List[Any]) -> List[Dict[str, Any]]:
        """Normalize animation keyframes and align their timeline to start at 0."""
        keyframes: List[Dict[str, Any]] = []
        for idx, frame in enumerate(frames):
            if not isinstance(frame, dict):
                continue
            profile = self._build_layer_color_profile(frame)
            if not profile:
                continue
            time_value = self._coerce_float(frame.get("time") or frame.get("t") or frame.get("frame"))
            if time_value is None:
                time_value = float(idx)
            keyframes.append(
                {
                    "time": time_value,
                    "color": profile["srgb"],
                    "hdr": profile["hdr"],
                    "hex": profile.get("hex"),
                    "interpolation": frame.get("interpolation") or frame.get("mode"),
                    "source": frame,
                }
            )
        if not keyframes:
            return []
        keyframes.sort(key=lambda item: item["time"])
        base_time = keyframes[0]["time"]
        for keyframe in keyframes:
            keyframe["time"] -= base_time
        return keyframes

    @staticmethod
    def _coerce_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                return float(stripped)
            except ValueError:
                return None
        return None

    def _load_costume_atlases(self, sheet_names: Set[str]) -> List[TextureAtlas]:
        """Load texture atlases referenced by a costume, reusing cached textures when possible."""
        atlases: List[TextureAtlas] = []
        if not sheet_names:
            return atlases
        data_root = os.path.join(self.game_path, "data")
        need_context = bool(sheet_names)
        if need_context:
            self.gl_widget.makeCurrent()
        try:
            for sheet in sheet_names:
                xml_path = self._resolve_costume_sheet_path(sheet)
                if not xml_path:
                    self.log_widget.log(f"Costume atlas not found: {sheet}", "WARNING")
                    continue
                norm = os.path.normcase(os.path.normpath(xml_path))
                atlas = self.costume_atlas_cache.get(norm)
                if not atlas:
                    atlas = TextureAtlas()
                    if not atlas.load_from_xml(xml_path, data_root):
                        self.log_widget.log(f"Failed to parse costume atlas: {sheet}", "ERROR")
                        continue
                    if not atlas.load_texture():
                        self.log_widget.log(f"Failed to upload costume atlas texture: {sheet}", "ERROR")
                        continue
                    atlas.source_name = sheet
                    self.costume_atlas_cache[norm] = atlas
                atlases.append(atlas)
        finally:
            if need_context:
                self.gl_widget.doneCurrent()
        return atlases

    def _resolve_costume_sheet_path(self, sheet: str) -> Optional[str]:
        """Resolve a costume XML path relative to multiple search roots."""
        if not sheet:
            return None
        if os.path.isabs(sheet) and os.path.exists(sheet):
            return sheet
        candidates = [
            os.path.join(self.game_path, "data", "xml_resources", sheet),
            os.path.join(self.game_path, "data", sheet)
        ]
        if self.current_json_path:
            base_dir = os.path.dirname(self.current_json_path)
            candidates.append(os.path.join(base_dir, sheet))
        candidates.append(os.path.join(str(self.project_root), sheet))
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        return None

    def _resolve_source_xml_path(self, xml_file: str, json_dir: Optional[str]) -> Optional[str]:
        """Return the absolute XML path for an animation source entry."""
        if not xml_file or not self.game_path:
            return None
        data_root = os.path.join(self.game_path, "data")
        candidates = [
            os.path.join(data_root, xml_file),
            os.path.join(data_root, "xml_resources", os.path.basename(xml_file)),
        ]
        if json_dir:
            candidates.append(os.path.join(json_dir, xml_file))
            candidates.append(os.path.join(json_dir, os.path.basename(xml_file)))
        candidates.append(os.path.join(str(self.project_root), xml_file))
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        return None

    def _load_texture_atlases_for_sources(
        self,
        sources: List[Dict[str, Any]],
        *,
        json_dir: Optional[str],
        use_cache: bool
    ) -> List[TextureAtlas]:
        """Create TextureAtlas objects for a set of source descriptors."""
        atlases: List[TextureAtlas] = []
        if not sources or not self.game_path:
            return atlases
        data_root = os.path.join(self.game_path, "data")
        for source in sources:
            xml_file = source.get("src")
            xml_path = self._resolve_source_xml_path(xml_file, json_dir)
            if not xml_path:
                self.log_widget.log(f"XML file not found: {xml_file}", "ERROR")
                continue
            norm = os.path.normcase(os.path.normpath(xml_path))
            atlas = self.costume_atlas_cache.get(norm) if use_cache else None
            created = False
            if not atlas:
                atlas = TextureAtlas()
                if not atlas.load_from_xml(xml_path, data_root):
                    self.log_widget.log(f"Failed to load texture atlas: {os.path.basename(xml_path)}", "ERROR")
                    continue
                atlas.source_name = source.get("src") or os.path.basename(xml_path)
                created = True
                if use_cache:
                    self.costume_atlas_cache[norm] = atlas
            source_id_value = source.get("id")
            if source_id_value is not None:
                try:
                    atlas.source_id = int(source_id_value)
                except (TypeError, ValueError):
                    atlas.source_id = None
            else:
                atlas.source_id = None
            atlases.append(atlas)
            if not use_cache or created:
                self.log_widget.log(f"Loaded texture atlas: {os.path.basename(xml_path)}", "SUCCESS")
        return atlases

    def _rebuild_source_atlas_lookup(
        self,
        sources: List[Dict[str, Any]],
        atlases: List[TextureAtlas]
    ) -> None:
        """Map animation source ids/names to their loaded TextureAtlas objects."""
        mapping: Dict[Any, TextureAtlas] = {}
        for atlas in atlases:
            source_id = getattr(atlas, "source_id", None)
            if source_id is not None and source_id not in mapping:
                mapping[source_id] = atlas
            source_name = getattr(atlas, "source_name", None)
            if source_name:
                lower = source_name.lower()
                mapping.setdefault(lower, atlas)
        if len(atlases) == len(sources):
            for idx, source in enumerate(sources):
                key = source.get("id")
                if key is None:
                    key = idx
                if key not in mapping and idx < len(atlases):
                    mapping[key] = atlases[idx]
        self.source_atlas_lookup = mapping

    def _prepare_costume_attachments(
        self, attachments: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Resolve attachment metadata so future renderer hooks can consume it."""
        prepared: List[Dict[str, Any]] = []
        if not attachments:
            return prepared
        for entry in attachments:
            target = entry.get("attach_to", "")
            resource = entry.get("resource", "")
            animation = entry.get("animation", "")
            raw_value = entry.get("time_offset", entry.get("time_scale", 0.0))
            time_offset, tempo_multiplier, loop_flag = self._extract_attachment_metadata(entry, raw_value)
            resolved = self._resolve_attachment_resource(resource)
            prepared.append(
                {
                    "attach_to": target,
                    "resource": resource,
                    "animation": animation,
                    "time_offset": time_offset,
                    # Keep legacy key for downstream consumers that still expect it.
                    "time_scale": time_offset,
                    "tempo_multiplier": tempo_multiplier,
                    "loop": loop_flag,
                    "root_layer": entry.get("root_layer") or entry.get("root_socket"),
                    "raw_time_value": entry.get("raw_time_value", raw_value),
                    **resolved,
                }
            )
        return prepared

    def _extract_attachment_metadata(
        self,
        entry: Dict[str, Any],
        default_time: Optional[float]
    ) -> Tuple[float, float, bool]:
        """Return sanitized (time_offset, tempo_multiplier, loop_flag)."""
        raw_value = entry.get("raw_time_value", default_time)
        try:
            time_offset = float(default_time)
        except (TypeError, ValueError):
            time_offset = 0.0
        tempo_multiplier = entry.get("tempo_multiplier")
        loop_flag = entry.get("loop")
        if tempo_multiplier is None and isinstance(raw_value, (int, float)):
            if 0.0 < abs(raw_value) <= 0.0025:
                tempo_multiplier = max(0.1, abs(raw_value) * 100.0)
                time_offset = 0.0
        if tempo_multiplier is None:
            tempo_multiplier = 1.0
        try:
            tempo_multiplier = float(tempo_multiplier)
        except (TypeError, ValueError):
            tempo_multiplier = 1.0
        tempo_multiplier = max(0.1, tempo_multiplier)
        if loop_flag is None:
            loop_flag = True
        else:
            loop_flag = bool(loop_flag)
        return time_offset, tempo_multiplier, loop_flag

    def _resolve_attachment_resource(self, resource: str) -> Dict[str, Optional[str]]:
        """Resolve candidate paths for an attachment resource."""
        if not resource:
            return {"bin_path": None, "json_path": None}
        raw = Path(resource)
        candidates: List[Path] = []
        if raw.is_absolute():
            candidates.append(raw)
        if self.game_path:
            game_root = Path(self.game_path)
            candidates.append(game_root / resource)
            candidates.append(game_root / "data" / resource)
            candidates.append(game_root / "data" / "xml_bin" / raw.name)
        candidates.append(Path(str(self.project_root)) / resource)
        candidates.append(Path(str(self.project_root)) / "Resources" / resource)

        bin_path: Optional[Path] = None
        json_path: Optional[Path] = None
        for cand in candidates:
            if cand.exists():
                if cand.suffix.lower() == ".json":
                    json_path = cand
                else:
                    bin_path = cand
                break

        if bin_path and not json_path:
            converted = bin_path.with_suffix(".json")
            if converted.exists():
                json_path = converted

        if not bin_path and resource.lower().endswith(".bin"):
            if raw.exists():
                bin_path = raw

        return {
            "bin_path": str(bin_path) if bin_path else None,
            "json_path": str(json_path) if json_path else None,
        }

    def _build_animation_struct(
        self,
        anim_dict: Dict[str, Any],
        blend_version: int,
        source_path: Optional[str] = None,
        resource_dict: Optional[Dict[str, Any]] = None
    ) -> AnimationData:
        """Convert a raw animation dictionary into AnimationData."""

        def _parse_color_tuple(raw_value: Any) -> Optional[Tuple[float, float, float, float]]:
            if isinstance(raw_value, (list, tuple)) and len(raw_value) == 4:
                try:
                    return tuple(float(component) for component in raw_value)
                except (TypeError, ValueError):
                    return None
            return None

        def _coerce_render_tags(raw_value: Any) -> Set[str]:
            tags: Set[str] = set()
            if isinstance(raw_value, (list, tuple, set)):
                for entry in raw_value:
                    if isinstance(entry, str) and entry.strip():
                        tags.add(entry.strip())
            elif isinstance(raw_value, str) and raw_value.strip():
                tags.add(raw_value.strip())
            return tags

        layers: List[LayerData] = []
        source_token = self._token_from_path(source_path) or self._current_monster_token()
        for layer_data in anim_dict.get('layers', []):
            keyframes: List[KeyframeData] = []
            for frame_data in layer_data.get('frames', []):
                keyframes.append(
                    KeyframeData(
                        time=frame_data.get('time', 0.0),
                        pos_x=frame_data.get('pos', {}).get('x', 0),
                        pos_y=frame_data.get('pos', {}).get('y', 0),
                        scale_x=frame_data.get('scale', {}).get('x', 100),
                        scale_y=frame_data.get('scale', {}).get('y', 100),
                        rotation=frame_data.get('rotation', {}).get('value', 0),
                        opacity=frame_data.get('opacity', {}).get('value', 100),
                        sprite_name=frame_data.get('sprite', {}).get('string', ''),
                        r=frame_data.get('rgb', {}).get('red', 255),
                        g=frame_data.get('rgb', {}).get('green', 255),
                        b=frame_data.get('rgb', {}).get('blue', 255),
                        immediate_pos=frame_data.get('pos', {}).get('immediate', 0),
                        immediate_scale=frame_data.get('scale', {}).get('immediate', 0),
                        immediate_rotation=frame_data.get('rotation', {}).get('immediate', 0),
                        immediate_opacity=frame_data.get('opacity', {}).get('immediate', 0),
                        immediate_sprite=frame_data.get('sprite', {}).get('immediate', 0),
                        immediate_rgb=frame_data.get('rgb', {}).get('immediate', -1)
                    )
                )
            blend_value = self._normalize_blend_value(layer_data.get('blend', 0), blend_version)
            if self._should_force_standard_blend(
                source_token,
                layer_data.get('name', ''),
                blend_value
            ):
                blend_value = BlendMode.STANDARD
            color_tint = _parse_color_tuple(layer_data.get('color_tint'))
            color_tint_hdr = _parse_color_tuple(layer_data.get('color_tint_hdr'))
            gradient_data = layer_data.get('color_gradient')
            animator_data = layer_data.get('color_animator')
            metadata = layer_data.get('color_metadata')
            render_tags = _coerce_render_tags(layer_data.get('render_tags'))
            mask_role = layer_data.get('mask_role')
            mask_key = layer_data.get('mask_key')
            layers.append(
                LayerData(
                    name=layer_data.get('name', ''),
                    layer_id=layer_data.get('id', 0),
                    parent_id=layer_data.get('parent', -1),
                    anchor_x=layer_data.get('anchor_x', 0.0),
                    anchor_y=layer_data.get('anchor_y', 0.0),
                    blend_mode=blend_value,
                    keyframes=keyframes,
                    visible=layer_data.get('visible', True),
                    shader_name=layer_data.get('shader'),
                    color_tint=color_tint,
                    color_tint_hdr=color_tint_hdr,
                    color_gradient=copy.deepcopy(gradient_data) if isinstance(gradient_data, dict) else None,
                    color_animator=copy.deepcopy(animator_data) if isinstance(animator_data, dict) else None,
                    color_metadata=copy.deepcopy(metadata) if isinstance(metadata, dict) else None,
                    render_tags=render_tags,
                    mask_role=str(mask_role) if isinstance(mask_role, str) and mask_role else None,
                    mask_key=str(mask_key) if isinstance(mask_key, str) and mask_key else None,
                )
            )
        animation = AnimationData(
            name=anim_dict.get('name', ''),
            width=anim_dict.get('width', 0),
            height=anim_dict.get('height', 0),
            loop_offset=anim_dict.get('loop_offset', 0.0),
            centered=anim_dict.get('centered', 0),
            layers=layers
        )
        self._apply_monster_layer_overrides(layers, source_token, resource_dict, source_path)
        return animation

    def _apply_monster_layer_overrides(
        self,
        layers: List[LayerData],
        source_token: Optional[str],
        resource_dict: Optional[Dict[str, Any]],
        source_path: Optional[str]
    ) -> None:
        """Inject per-monster layer tweaks that the stock JSON export omits."""
        if not layers or not source_token:
            return
        token = source_token.lower()
        if token == "gjlm":
            self._apply_gjlm_mouth_overrides(layers, token, resource_dict, source_path)

    def _apply_gjlm_mouth_overrides(
        self,
        layers: List[LayerData],
        token: str,
        resource_dict: Optional[Dict[str, Any]],
        source_path: Optional[str]
    ) -> None:
        """Fallback handling for the GJLM mouth layer when no special shader is active."""
        mouth_layers = [
            layer for layer in layers
            if (layer.name or "").strip().lower() == "mouth"
        ]
        if not mouth_layers:
            return
        for layer in mouth_layers:
            layer.color_tint = (1.0, 1.0, 1.0, 1.0)
            layer.mask_role = None
            layer.mask_key = None
        lookup = {layer.name.lower(): layer for layer in layers if layer.name}
        shadow_layer = lookup.get("shadow")
        if shadow_layer:
            shadow_layer.mask_role = None
            shadow_layer.mask_key = None

    def _load_rev6_animation_module(self):
        """Dynamically import the rev6-2-json converter so we can parse BIN files."""
        if self._rev6_anim_module is not None:
            return self._rev6_anim_module
        script_path = self.project_root / "Resources" / "bin2json" / "rev6-2-json.py"
        if not script_path.exists():
            self.log_widget.log("rev6-2-json script missing; attachment animations cannot be converted.", "ERROR")
            return None
        spec = importlib.util.spec_from_file_location("msm_rev6_anim", script_path)
        if not spec or not spec.loader:
            self.log_widget.log("Failed to load rev6-2-json script.", "ERROR")
            return None
        script_dir = str(script_path.parent)
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[attr-defined]
        self._rev6_anim_module = module
        return module

    def _load_animation_resource_dict(
        self, json_path: Optional[str], bin_path: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """Return a parsed animation dictionary from either JSON or BIN paths."""
        source_path = json_path or bin_path
        if not source_path:
            return None
        norm = os.path.normcase(os.path.normpath(source_path))
        cached = self.attachment_animation_cache.get(norm)
        if cached:
            return cached
        data: Optional[Dict[str, Any]] = None
        try:
            if json_path and os.path.exists(json_path):
                with open(json_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            elif bin_path and os.path.exists(bin_path):
                module = self._load_rev6_animation_module()
                if not module:
                    return None
                bin_anim = module.BinAnim.from_file(str(bin_path))
                data = bin_anim.to_dict()
        except Exception as exc:
            self.log_widget.log(f"Failed to load attachment animation from {source_path}: {exc}", "ERROR")
            return None
        if data is None:
            self.log_widget.log(f"Attachment animation source not found: {source_path}", "WARNING")
            return None
        self.attachment_animation_cache[norm] = data
        return data

    def _build_attachment_payloads(
        self,
        attachments: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Convert attachment metadata into payloads for the renderer."""
        payloads: List[Dict[str, Any]] = []
        if not attachments:
            return payloads
        for entry in attachments:
            anim_dict = self._load_animation_resource_dict(entry.get("json_path"), entry.get("bin_path"))
            if not anim_dict:
                continue
            anim_name = entry.get("animation") or ""
            target_anim = None
            for candidate in anim_dict.get("anims", []):
                if not anim_name or candidate.get("name") == anim_name:
                    target_anim = candidate
                    break
            if target_anim is None:
                self.log_widget.log(
                    f"Attachment animation '{anim_name}' not found in {entry.get('resource')}",
                    "WARNING"
                )
                continue
            blend_version = anim_dict.get("blend_version", self.current_blend_version or 1)
            animation_data = self._build_animation_struct(
                target_anim,
                blend_version,
                entry.get("json_path") or entry.get("bin_path"),
                resource_dict=anim_dict
            )
            json_dir = os.path.dirname(entry.get("json_path")) if entry.get("json_path") else None
            atlases = self._load_texture_atlases_for_sources(
                anim_dict.get("sources", []),
                json_dir=json_dir,
                use_cache=True
            )
            if not atlases:
                self.log_widget.log(
                    f"Attachment '{anim_name or entry.get('attach_to')}' has no texture atlases; skipping.",
                    "WARNING"
                )
                continue
            raw_offset = entry.get("time_offset", entry.get("time_scale", 0.0))
            try:
                offset_value = float(raw_offset)
            except (TypeError, ValueError):
                offset_value = 0.0
            if not math.isfinite(offset_value):
                offset_value = 0.0
            tempo_multiplier = entry.get("tempo_multiplier", 1.0)
            try:
                tempo_multiplier = float(tempo_multiplier)
            except (TypeError, ValueError):
                tempo_multiplier = 1.0
            tempo_multiplier = max(0.1, tempo_multiplier)
            loop_flag = bool(entry.get("loop", True))
            root_layer_name = self._determine_attachment_root(entry.get("root_layer"), animation_data)
            payloads.append(
                {
                    "name": anim_name or entry.get("attach_to") or "attachment",
                    "target_layer": entry.get("attach_to", ""),
                    "target_layer_id": None,
                    "animation": animation_data,
                    "atlases": atlases,
                    "time_offset": offset_value,
                    "time_scale": offset_value,
                    "tempo_multiplier": tempo_multiplier,
                    "loop": loop_flag,
                    "root_layer": root_layer_name,
                }
            )
        return payloads

    def _determine_attachment_root(
        self,
        preferred: Optional[str],
        animation: AnimationData
    ) -> Optional[str]:
        """Return the attachment root layer name, preferring user-authored sockets."""
        if not animation or not animation.layers:
            return preferred
        if preferred:
            lowered = preferred.lower()
            for layer in animation.layers:
                if layer.name.lower() == lowered:
                    return layer.name
        for layer in animation.layers:
            if layer.parent_id < 0:
                return layer.name
        return preferred

    def _assign_attachment_targets(
        self,
        payloads: List[Dict[str, Any]],
        layers: List[LayerData]
    ) -> None:
        """Resolve attachment target names to actual layer ids."""
        if not payloads:
            return
        lookup = {layer.name.lower(): layer for layer in layers}
        for payload in payloads:
            target = payload.get("target_layer", "")
            if not target:
                continue
            layer = self._find_layer_in_lookup(lookup, (target,))
            if layer:
                payload["target_layer_id"] = layer.layer_id
            else:
                self.log_widget.log(
                    f"Attachment target '{target}' not found; attachment '{payload.get('name')}' will be skipped.",
                    "WARNING"
                )
    
    def convert_bin_to_json(self):
        """Convert selected BIN file to JSON"""
        current_data = self.control_panel.bin_combo.currentData()
        current_text = self.control_panel.bin_combo.currentText()

        if current_data:
            bin_path = current_data
        elif current_text:
            bin_path = os.path.join(self.game_path, "data", "xml_bin", current_text)
        else:
            bin_path = ""

        if not bin_path or not bin_path.lower().endswith('.bin'):
            QMessageBox.warning(self, "Error", "Please select a .bin file")
            return
        
        if not os.path.exists(bin_path):
            QMessageBox.warning(self, "Error", "Selected BIN file no longer exists")
            self.log_widget.log(f"Missing BIN file: {bin_path}", "ERROR")
            return

        json_path = self._convert_bin_file(bin_path, announce=True)
        if json_path and not self.select_file_by_path(json_path):
            self.log_widget.log("Could not auto-select converted JSON file", "WARNING")

    def _convert_bin_file(self, bin_path: str, *, force: bool = False, announce: bool = True) -> Optional[str]:
        """Convert a specific BIN file via bin2json, optionally forcing re-export."""
        if not self.bin2json_path:
            if announce:
                QMessageBox.warning(self, "Error", "bin2json script not found")
            self.log_widget.log("bin2json script not found; conversion skipped.", "ERROR")
            return None

        if not os.path.exists(bin_path):
            self.log_widget.log(f"BIN file not found: {bin_path}", "ERROR")
            if announce:
                QMessageBox.warning(self, "Error", "Selected BIN file no longer exists")
            return None

        relative_display = os.path.relpath(
            bin_path,
            os.path.join(self.game_path, "data", "xml_bin")
        ).replace("\\", "/")
        action = "Re-exporting" if force else "Converting"

        try:
            self.log_widget.log(f"{action} {relative_display} to JSON...", "INFO")
            result = subprocess.run(
                [sys.executable, self.bin2json_path, 'd', bin_path],
                capture_output=True,
                text=True,
                cwd=os.path.dirname(self.bin2json_path)
            )
            if result.returncode != 0:
                self.log_widget.log(f"Conversion failed: {result.stderr}", "ERROR")
                if announce:
                    QMessageBox.warning(self, "Conversion Failed", result.stderr or "Unknown error")
                return None
            json_path = os.path.splitext(bin_path)[0] + '.json'
            self.log_widget.log(f"Successfully converted {relative_display}", "SUCCESS")
            self.refresh_file_list()
            return json_path
        except Exception as exc:
            self.log_widget.log(f"Error converting file: {exc}", "ERROR")
            if announce:
                QMessageBox.warning(self, "Error", str(exc))
        return None
    
    def on_bin_selected(self, index: int):
        """Handle BIN/JSON file selection"""
        if index < 0:
            return
        
        selected_path = self.control_panel.bin_combo.currentData()
        display_name = self.control_panel.bin_combo.currentText()
        
        if not selected_path and display_name:
            selected_path = os.path.join(self.game_path, "data", "xml_bin", display_name)
        
        if not selected_path:
            return
        
        if selected_path.lower().endswith('.json'):
            self.load_json_file(selected_path)
        elif selected_path.lower().endswith('.bin'):
            self.log_widget.log("Please convert BIN to JSON first", "WARNING")
    
    def load_json_file(self, json_path: str):
        """Load animation data from JSON file"""
        try:
            with open(json_path, 'r') as f:
                payload = json.load(f)
            self._apply_json_payload(json_path, payload)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(tb)
            self.log_widget.log(f"Error loading JSON: {e}", "ERROR")
            self.log_widget.log(tb, "ERROR")

    def _apply_json_payload(self, json_path: str, payload: Dict[str, Any], announce: bool = True) -> None:
        """Apply a parsed JSON payload to the UI and animation combo."""
        self.current_json_path = json_path
        self.current_json_data = payload
        self.original_json_data = copy.deepcopy(payload)
        self.current_blend_version = self._determine_blend_version(payload)
        self.source_atlas_lookup = {}
        if announce:
            display_name = os.path.basename(json_path) if json_path else "animation data"
            self.log_widget.log(f"Loaded JSON file: {display_name}", "SUCCESS")
            self.log_widget.log(
                f"Detected blend mapping version {self.current_blend_version}",
                "INFO"
            )

        self.control_panel.anim_combo.clear()
        if 'anims' in payload:
            anim_names = [anim.get('name', f"Animation {idx + 1}") for idx, anim in enumerate(payload['anims'])]
            if anim_names:
                self.control_panel.anim_combo.addItems(anim_names)
                if announce:
                    self.log_widget.log(f"Found {len(anim_names)} animations", "INFO")

    def _normalize_animation_file_payload(self, payload: Any) -> Optional[Dict[str, Any]]:
        """Coerce arbitrary animation exports into the canonical JSON schema."""
        if not isinstance(payload, dict):
            return None
        anims = payload.get("anims")
        if isinstance(anims, list):
            return payload
        layers = payload.get("layers")
        if isinstance(layers, list):
            anim_copy = copy.deepcopy(payload)
            sources = anim_copy.pop("sources", payload.get("sources", []))
            blend_version = anim_copy.pop("blend_version", payload.get("blend_version"))
            rev_value = anim_copy.pop("rev", payload.get("rev"))
            container = {
                "anims": [anim_copy],
                "sources": sources if isinstance(sources, list) else []
            }
            if blend_version is not None:
                container["blend_version"] = blend_version
            else:
                container["blend_version"] = self.current_blend_version or 1
            if rev_value is not None:
                container["rev"] = rev_value
            return container
        return None

    def save_animation_to_file(self):
        """Export the currently loaded animation to a standalone JSON file."""
        animation = getattr(self.gl_widget.player, "animation", None)
        if not animation:
            QMessageBox.warning(self, "No Animation", "Load an animation before saving.")
            return
        default_path = self.settings.value('animation/last_save_path', '', type=str) or ''
        if not default_path:
            base_dir = os.path.dirname(self.current_json_path) if self.current_json_path else str(Path.home())
            base_name = (animation.name or "animation").strip() or "animation"
            safe_name = re.sub(r'[\\\\/:"*?<>|]+', "_", base_name)
            default_path = os.path.join(base_dir, f"{safe_name}.json")
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save Animation",
            default_path,
            "Animation JSON (*.json);;All Files (*)"
        )
        if not filename:
            return
        if not filename.lower().endswith(".json"):
            filename += ".json"
        if self.current_json_data:
            payload = copy.deepcopy(self.current_json_data)
        else:
            payload = {
                "blend_version": self.current_blend_version or 1,
                "sources": [],
                "anims": [self._export_animation_dict(animation)],
                "rev": 6,
            }
        try:
            with open(filename, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
        except Exception as exc:
            self.log_widget.log(f"Failed to save animation: {exc}", "ERROR")
            QMessageBox.warning(self, "Save Failed", f"Could not save animation:\n{exc}")
            return
        self.settings.setValue('animation/last_save_path', filename)
        self.log_widget.log(f"Saved animation to {os.path.basename(filename)}", "SUCCESS")
        if getattr(self.export_settings, "update_source_json_on_save", False) and self.current_json_path:
            try:
                with open(self.current_json_path, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2)
                self.original_json_data = copy.deepcopy(payload)
                self.log_widget.log(
                    f"Updated source JSON: {os.path.basename(self.current_json_path)}",
                    "INFO",
                )
            except Exception as exc:
                self.log_widget.log(
                    f"Failed to update source JSON '{self.current_json_path}': {exc}",
                    "WARNING",
                )

    def export_animation_to_bin(self):
        """Convert the current animation into a BIN file using the bin2json script."""
        animation = getattr(self.gl_widget.player, "animation", None)
        if not animation:
            QMessageBox.warning(self, "No Animation", "Load an animation before exporting a BIN.")
            return
        if not self.bin2json_path or not os.path.exists(self.bin2json_path):
            QMessageBox.warning(self, "Missing Tool", "bin2json script was not found; cannot export BIN.")
            return
        default_path = self.settings.value('animation/last_bin_export', '', type=str) or ''
        if not default_path:
            base_dir = os.path.dirname(self.current_json_path) if self.current_json_path else str(Path.home())
            base_name = (animation.name or "animation").strip() or "animation"
            safe_name = re.sub(r'[\\/:\"*?<>|]+', "_", base_name)
            default_path = os.path.join(base_dir, f"{safe_name}.bin")
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export Animation BIN",
            default_path,
            "Animation BIN (*.bin);;All Files (*)"
        )
        if not filename:
            return
        if not filename.lower().endswith(".bin"):
            filename += ".bin"
        has_pending_edits = self._has_pending_json_edits()
        passthrough_json: Optional[str] = None
        if (
            not has_pending_edits
            and self.current_json_path
            and os.path.exists(self.current_json_path)
        ):
            passthrough_json = self.current_json_path

        payload: Optional[Dict[str, Any]] = None
        if not passthrough_json:
            if self.current_json_data:
                payload = copy.deepcopy(self.current_json_data)
            elif self.current_json_path and os.path.exists(self.current_json_path):
                try:
                    with open(self.current_json_path, "r", encoding="utf-8") as handle:
                        payload = json.load(handle)
                except Exception:
                    payload = None
            if not payload:
                payload = {
                    "blend_version": self.current_blend_version or 1,
                    "sources": [],
                    "anims": [],
                }
            self._inject_animation_into_payload(payload, animation)
            self.log_widget.log(
                "Merged current edits into export payload.",
                "DEBUG"
            )
        else:
            self.log_widget.log(
                "No edits detected; exporting original JSON payload.",
                "DEBUG"
            )

        tmp_dir: Optional[tempfile.TemporaryDirectory] = None
        try:
            tmp_dir = tempfile.TemporaryDirectory()
            temp_json = os.path.join(tmp_dir.name, "animation.json")
            if passthrough_json:
                shutil.copy2(passthrough_json, temp_json)
            else:
                with open(temp_json, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2)
            result = subprocess.run(
                [sys.executable, self.bin2json_path, 'b', temp_json],
                capture_output=True,
                text=True,
                cwd=os.path.dirname(self.bin2json_path)
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "bin2json conversion failed.")
            temp_bin = os.path.splitext(temp_json)[0] + ".bin"
            if not os.path.exists(temp_bin):
                raise RuntimeError("bin2json did not produce a BIN file.")
            os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)
            shutil.move(temp_bin, filename)
        except Exception as exc:
            if tmp_dir:
                tmp_dir.cleanup()
            self.log_widget.log(f"Failed to export animation BIN: {exc}", "ERROR")
            QMessageBox.warning(self, "Export Failed", f"Could not create animation BIN:\n{exc}")
            return
        if tmp_dir:
            tmp_dir.cleanup()
        self.settings.setValue('animation/last_bin_export', filename)
        self.log_widget.log(f"Exported animation BIN to {os.path.basename(filename)}", "SUCCESS")

    def load_saved_animation(self):
        """Load an animation JSON exported from the viewer or the game."""
        last_load = self.settings.value('animation/last_load_path', '', type=str) or ''
        if not last_load:
            last_load = self.settings.value('animation/last_save_path', '', type=str) or ''
        if not last_load:
            last_load = self.current_json_path or str(Path.home())
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Load Animation",
            last_load,
            "Animation JSON (*.json);;All Files (*)"
        )
        if not filename:
            return
        try:
            with open(filename, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            self.log_widget.log(f"Failed to open animation file: {exc}", "ERROR")
            QMessageBox.warning(self, "Load Failed", f"Could not read animation file:\n{exc}")
            return
        normalized = self._normalize_animation_file_payload(payload)
        if not normalized:
            self.log_widget.log("Selected file does not contain animation data.", "ERROR")
            QMessageBox.warning(self, "Invalid File", "Selected file does not contain animation data.")
            return
        self._apply_json_payload(filename, normalized)
        self.settings.setValue('animation/last_load_path', filename)
        self.settings.setValue('animation/last_save_path', filename)
        combo = self.control_panel.anim_combo
        if combo.count() == 0:
            self.log_widget.log("Loaded file contains no animations.", "WARNING")
            return
        combo.blockSignals(True)
        combo.setCurrentIndex(0)
        combo.blockSignals(False)
        self.on_animation_selected(0)

    def _determine_blend_version(self, json_data: Dict) -> int:
        """
        Decide which blend mapping the source JSON uses.
        Older exports only included blend ids 0/1 where 1 represented additive layers.
        Newer exports (bin2json rev6-2) tag additive layers as 2 and include a
        'blend_version' metadata field. We need to support both so existing libraries
        render correctly.
        """
        version = json_data.get('blend_version')
        has_high_blend = False
        for anim in json_data.get('anims', []):
            for layer in anim.get('layers', []):
                if layer.get('blend', 0) >= 2:
                    has_high_blend = True
                    break
            if has_high_blend:
                break
        if isinstance(version, int) and version >= 2 and has_high_blend:
            return version
        return 1

    @staticmethod
    def _normalize_blend_value(raw_value: int, version: int) -> int:
        """
        Convert raw blend ids from JSON into the canonical renderer mapping.
        Version 1 (legacy) used 0=standard, 1=additive.
        Version 2+ matches the game's BlendType enum directly.
        """
        if version >= 2:
            return raw_value
        # Legacy exports only distinguished between the default blend (0) and a single
        # alternate mode (1). In practice, files authored with this format used `1` for
        # additive-style glows, so map it to the additive id in the modern enum.
        return 2 if raw_value == 1 else 0

    def _current_json_cache_key(self) -> Optional[str]:
        """Return normalized path key for per-JSON caches."""
        if not self.current_json_path:
            return None
        return os.path.normcase(os.path.normpath(self.current_json_path))

    def _has_pending_json_edits(self) -> bool:
        """Return True if the in-memory JSON differs from the original baseline."""
        if self.current_json_data is None:
            return False
        if self.original_json_data is None:
            return True
        try:
            return self.current_json_data != self.original_json_data
        except Exception:
            return True

    @staticmethod
    def _token_from_path(source_path: Optional[str]) -> Optional[str]:
        """Return the monster token inferred from a JSON/BIN path."""
        if not source_path:
            return None
        try:
            stem = Path(source_path).stem
        except Exception:
            return None
        if not stem:
            return None
        token = stem.lower()
        if token.startswith("monster_"):
            token = token[8:]
        return token or None

    def _should_force_standard_blend(
        self,
        token: Optional[str],
        layer_name: str,
        blend_mode: int
    ) -> bool:
        """Return True if a layer's blend mode should be overridden for compatibility."""
        return False

    def _apply_cached_layer_visibility(self, layers: List[LayerData]):
        """Apply stored layer visibility values for the active JSON."""
        cache_key = self._current_json_cache_key()
        if not cache_key:
            return
        visibility_map = self.layer_visibility_cache.get(cache_key)
        if not visibility_map:
            return
        for layer in layers:
            if layer.layer_id in visibility_map:
                layer.visible = visibility_map[layer.layer_id]

    def _remember_layer_visibility(self, layer: LayerData):
        """Persist a layer's visibility so other animations reuse it."""
        cache_key = self._current_json_cache_key()
        if not cache_key:
            return
        visibility_map = self.layer_visibility_cache.setdefault(cache_key, {})
        visibility_map[layer.layer_id] = layer.visible

    def _record_layer_defaults(self, layers: List[LayerData]):
        """Capture the default ordering and visibility for the current animation state."""
        self._default_layer_order = [layer.layer_id for layer in layers]
        self._default_layer_visibility = {layer.layer_id: layer.visible for layer in layers}
        self._default_hidden_layer_ids = {layer.layer_id for layer in layers if not layer.visible}
        self.layer_panel.set_default_hidden_layers(self._default_hidden_layer_ids)
        self._capture_pose_baseline()

    def _capture_pose_baseline(self):
        """Snapshot the current animation layers for pose reset/undo reference."""
        animation = getattr(self.gl_widget.player, "animation", None)
        if not animation:
            self._pose_baseline_player = None
            self._pose_baseline_lookup = {}
            return
        baseline_layers = self._clone_layers(animation.layers)
        baseline_anim = AnimationData(
            animation.name,
            animation.width,
            animation.height,
            animation.loop_offset,
            animation.centered,
            baseline_layers,
        )
        self._pose_baseline_player = AnimationPlayer()
        # Ensure baseline player respects current tweening setting
        try:
            current_tween = getattr(self.gl_widget.player, 'tweening_enabled', True)
        except Exception:
            current_tween = True
        self._pose_baseline_player.tweening_enabled = current_tween
        self._pose_baseline_player.load_animation(baseline_anim)
        self._pose_baseline_lookup = {layer.layer_id: layer for layer in baseline_layers}

    def _get_pose_baseline_state(self, layer_id: int, time_value: float) -> Optional[Dict[str, Any]]:
        """Return the baseline layer local state for a given time."""
        if not self._pose_baseline_player:
            return None
        layer = self._pose_baseline_lookup.get(layer_id)
        if not layer:
            return None
        return self._pose_baseline_player.get_layer_state(layer, time_value)

    def _load_audio_preferences_from_storage(self):
        """Populate audio preference flags from QSettings."""
        self.sync_audio_to_bpm = self.settings.value('audio/sync_to_bpm', True, type=bool)
        self.pitch_shift_enabled = self.settings.value('audio/pitch_shift_enabled', False, type=bool)
        self.chipmunk_mode = self.settings.value('audio/chipmunk_mode', False, type=bool)

    def _load_base_bpm_overrides(self) -> None:
        """Load persisted base BPM overrides per monster token."""
        blob = self.settings.value('audio/base_bpm_overrides', '{}', type=str) or '{}'
        overrides: Dict[str, float] = {}
        try:
            data = json.loads(blob)
        except Exception:
            data = {}
        if isinstance(data, dict):
            for key, value in data.items():
                try:
                    overrides[str(key).lower()] = float(value)
                except (TypeError, ValueError):
                    continue
        self.monster_base_bpm_overrides = overrides

    def _save_base_bpm_overrides(self) -> None:
        """Persist monster base BPM overrides to QSettings."""
        try:
            blob = json.dumps(self.monster_base_bpm_overrides)
        except Exception:
            blob = "{}"
        self.settings.setValue('audio/base_bpm_overrides', blob)

    def _apply_audio_preferences_to_controls(self):
        """Sync control panel toggles and audio engine with stored preferences."""
        if hasattr(self, 'control_panel'):
            self.control_panel.set_sync_audio_checkbox(self.sync_audio_to_bpm)
            self.control_panel.set_pitch_shift_checkbox(self.pitch_shift_enabled)
        self._update_audio_speed()

    def _set_current_bpm(self, value: float, *, update_ui: bool = True, store_override: bool = False):
        """Apply BPM changes, optionally updating UI and storing overrides."""
        clamped = max(20.0, min(300.0, float(value)))
        self.current_bpm = clamped
        if update_ui:
            self.control_panel.set_bpm_value(clamped)
        base = max(1e-3, self.current_base_bpm)
        self._start_hang_watchdog("update_bpm", timeout=12.0)
        self.gl_widget.player.set_playback_speed(clamped / base)
        self._stop_hang_watchdog()
        if store_override and self.current_animation_name:
            self.animation_bpm_overrides[self.current_animation_name] = clamped
        self._update_audio_speed()

    def _configure_animation_bpm(self):
        """Detect and apply BPM for the current animation."""
        detected = self._detect_bpm_for_current_animation()
        token = self._current_monster_token()
        token_key = token.lower() if token else None
        override_base = self.monster_base_bpm_overrides.get(token_key) if token_key else None
        if override_base:
            self.current_base_bpm = float(override_base)
            if token:
                self.log_widget.log(
                    f"Using locked BPM {self.current_base_bpm:.1f} for {token}.",
                    "INFO",
                )
        elif detected:
            self.current_base_bpm = detected
            self.log_widget.log(f"Detected BPM {detected:.1f}", "INFO")
        else:
            self.current_base_bpm = 120.0
            self.log_widget.log("BPM detection failed, defaulting to 120", "WARNING")

        initial_value = self.current_base_bpm
        if self.current_animation_name:
            initial_value = self.animation_bpm_overrides.get(self.current_animation_name, initial_value)
        self._set_current_bpm(initial_value, update_ui=True, store_override=False)

    def _update_audio_speed(self):
        """Sync audio playback speed with current BPM settings."""
        if not self.audio_manager.is_ready:
            return
        if self.sync_audio_to_bpm:
            speed = self.current_bpm / max(1e-3, self.current_base_bpm)
        else:
            speed = 1.0

        if not self.sync_audio_to_bpm or abs(speed - 1.0) < 1e-3 or not self.pitch_shift_enabled:
            pitch_mode = "time_stretch"
        else:
            pitch_mode = "chipmunk" if self.chipmunk_mode else "pitch_shift"

        self._start_hang_watchdog("update_audio_speed", timeout=12.0)
        self.audio_manager.configure_playback(speed, pitch_mode)
        self._stop_hang_watchdog()

    def _get_export_playback_speed(self) -> float:
        """Return playback multiplier used for exports (mirrors UI BPM)."""
        player = getattr(self.gl_widget, "player", None)
        speed = getattr(player, "playback_speed", 1.0) if player else 1.0
        if speed <= 1e-3:
            base = max(1e-3, self.current_base_bpm)
            speed = self.current_bpm / base if self.current_bpm > 0 else 1.0
        return max(1e-3, float(speed))

    def _get_export_real_duration(self) -> float:
        """Return animation duration adjusted for the export playback speed."""
        player = getattr(self.gl_widget, "player", None)
        duration = getattr(player, "duration", 0.0) if player else 0.0
        speed = self._get_export_playback_speed()
        return duration / speed if speed > 1e-6 else duration

    def _get_export_frame_time(self, frame_index: int, fps: float) -> float:
        """Map the export frame index to the underlying animation time."""
        player = getattr(self.gl_widget, "player", None)
        if not player or fps <= 0:
            return 0.0
        base_duration = getattr(player, "duration", 0.0) or 0.0
        video_time = frame_index / float(fps)
        animation_time = video_time * self._get_export_playback_speed()
        if base_duration <= 0:
            return animation_time
        return min(base_duration, animation_time)

    def _get_audio_export_config(self) -> Tuple[float, str]:
        """Return (speed, pitch_mode) to mirror audio playback settings for exports."""
        if self.sync_audio_to_bpm:
            speed = self.current_bpm / max(1e-3, self.current_base_bpm)
        else:
            speed = 1.0
        if not self.sync_audio_to_bpm or abs(speed - 1.0) < 1e-3 or not self.pitch_shift_enabled:
            pitch_mode = "time_stretch"
        else:
            pitch_mode = "chipmunk" if self.chipmunk_mode else "pitch_shift"
        return speed, pitch_mode

    def _start_hang_watchdog(self, label: str, timeout: float = 12.0):
        """Arm faulthandler watchdog to print stack traces if we hang."""
        if self._hang_watchdog_active:
            return
        try:
            faulthandler.dump_traceback_later(timeout, repeat=True)
            self._hang_watchdog_active = True
            print(f"[WATCHDOG] Armed for {label} ({timeout}s)")
        except Exception as exc:  # pragma: no cover
            print(f"[WATCHDOG] Failed to arm for {label}: {exc}")

    def _stop_hang_watchdog(self):
        """Disarm hang watchdog if active."""
        if not self._hang_watchdog_active:
            return
        try:
            faulthandler.cancel_dump_traceback_later()
        except Exception as exc:  # pragma: no cover
            print(f"[WATCHDOG] Failed to cancel: {exc}")
        finally:
            self._hang_watchdog_active = False

    def _detect_bpm_for_current_animation(self) -> Optional[float]:
        """Return BPM derived from the island MIDI file, if any."""
        midi_path = self._resolve_midi_path_for_current_animation()
        if not midi_path:
            return None
        return self._read_midi_bpm(midi_path)

    def _resolve_midi_path_for_current_animation(self) -> Optional[str]:
        """Find the MIDI file associated with the current island."""
        if not self.game_path:
            return None
        music_dir = os.path.join(self.game_path, "data", "audio", "music")
        if not os.path.exists(music_dir):
            return None
        for code in self._build_island_code_candidates():
            if code is None:
                continue
            midi_path = self._find_midi_for_code(music_dir, code)
            if midi_path:
                return midi_path
        return None

    def _build_island_code_candidates(self) -> List[Optional[int]]:
        """Compile likely numeric prefixes for MIDI lookup."""
        candidates: List[Optional[int]] = []
        raw_code: Optional[int] = None
        if self.current_audio_path:
            raw_code = self._extract_numeric_prefix(os.path.basename(self.current_audio_path))
        if raw_code is None and self.current_animation_name:
            raw_code = self._extract_numeric_prefix(os.path.basename(self.current_animation_name))

        if raw_code is not None:
            candidates.append(raw_code)
            if raw_code >= 100:
                base_code = raw_code % 100
                if base_code != raw_code:
                    candidates.append(base_code)
        if not candidates:
            candidates.append(None)
        return candidates

    @staticmethod
    def _extract_numeric_prefix(value: str) -> Optional[int]:
        """Return the leading integer from a string (or world### pattern)."""
        if not value:
            return None
        match = re.match(r'^(\d+)', value)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
        match = re.search(r'world\s*([0-9]+)', value, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
        return None

    @staticmethod
    def _find_midi_for_code(music_dir: str, code: int) -> Optional[str]:
        """Check multiple filename variants for a `worldXX.mid` file."""
        names = {
            f"world{code}",
            f"world{code:02d}",
            f"world{code:03d}",
            f"World{code}",
            f"World{code:02d}",
            f"World{code:03d}",
            f"WORLD{code}",
            f"WORLD{code:02d}",
            f"WORLD{code:03d}",
        }
        for name in names:
            midi_path = os.path.join(music_dir, f"{name}.mid")
            if os.path.exists(midi_path):
                return midi_path
        return None

    @staticmethod
    def _read_midi_bpm(midi_path: str) -> Optional[float]:
        """Parse a MIDI file and return the first tempo event's BPM."""
        try:
            with open(midi_path, 'rb') as f:
                data = f.read()
        except OSError:
            return None
        if len(data) < 14 or data[0:4] != b'MThd':
            return None
        header_len = int.from_bytes(data[4:8], 'big')
        offset = 8 + header_len
        if offset > len(data):
            return None

        def read_varlen(chunk: bytes, idx: int) -> Tuple[int, int]:
            value = 0
            while idx < len(chunk):
                byte = chunk[idx]
                idx += 1
                value = (value << 7) | (byte & 0x7F)
                if not (byte & 0x80):
                    break
            return value, idx

        while offset + 8 <= len(data):
            if data[offset:offset + 4] != b'MTrk':
                # Unknown chunk
                chunk_len = int.from_bytes(data[offset + 4:offset + 8], 'big')
                offset += 8 + chunk_len
                continue

            track_len = int.from_bytes(data[offset + 4:offset + 8], 'big')
            offset += 8
            track_data = data[offset:offset + track_len]
            offset += track_len

            idx = 0
            running_status = None
            while idx < len(track_data):
                delta, idx = read_varlen(track_data, idx)
                if idx >= len(track_data):
                    break
                status = track_data[idx]
                if status >= 0x80:
                    idx += 1
                    running_status = status
                else:
                    status = running_status
                if status is None:
                    break

                if status == 0xFF:
                    if idx >= len(track_data):
                        break
                    meta_type = track_data[idx]
                    idx += 1
                    length, idx = read_varlen(track_data, idx)
                    meta_data = track_data[idx:idx + length]
                    idx += length
                    if meta_type == 0x51 and length == 3:
                        tempo_micro = int.from_bytes(meta_data, 'big')
                        if tempo_micro > 0:
                            return 60000000.0 / tempo_micro
                        return None
                elif status in (0xF0, 0xF7):
                    length, idx = read_varlen(track_data, idx)
                    idx += length
                else:
                    event_type = status & 0xF0
                    if event_type in (0xC0, 0xD0):
                        idx += 1
                    else:
                        idx += 2
        return None
    
    def on_animation_selected(self, index: int):
        """Handle animation selection"""
        if index < 0 or not self.current_json_data:
            return
        
        self.current_animation_index = index
        self.load_animation(index)

    def on_costume_selected(self, index: int):
        """Handle costume dropdown changes."""
        if not self.gl_widget.player.animation:
            return
        combo = self.control_panel.costume_combo
        if index < 0 or index >= combo.count():
            return
        key = combo.itemData(index)
        self.control_panel.set_costume_convert_enabled(key is not None)
        if not key:
            if self.active_costume_key is not None:
                self.log_widget.log("Reverted to base appearance", "INFO")
            self._apply_costume_to_animation(None)
            return
        entry = self.costume_entry_map.get(key)
        if not entry:
            self.log_widget.log("Selected costume is unavailable, refreshing list...", "WARNING")
            self._refresh_costume_list()
            entry = self.costume_entry_map.get(key)
            if not entry:
                self.log_widget.log("Unable to resolve costume selection.", "ERROR")
                return
            was_blocked = combo.blockSignals(True)
            for idx in range(combo.count()):
                if combo.itemData(idx) == entry.key:
                    combo.setCurrentIndex(idx)
                    break
            combo.blockSignals(was_blocked)
        if self.active_costume_key == entry.key:
            return
        self.log_widget.log(f"Applying costume: {entry.display_name}", "INFO")
        self._apply_costume_to_animation(entry)

    def load_animation(self, anim_index: int):
        """Load and display an animation"""
        if not self.current_json_data or 'anims' not in self.current_json_data:
            return

        preserved_costume_entry: Optional[CostumeEntry] = None
        previous_costume_key = self.active_costume_key
        self.layer_source_lookup = {}

        self.control_panel.set_pose_controls_enabled(False)
        self._start_hang_watchdog("load_animation")
        try:
            self.current_animation_embedded_clones = None
            anim_data = self.current_json_data['anims'][anim_index]
            self.current_animation_embedded_clones = self._extract_embedded_clone_defs(anim_data)
            sources = self.current_json_data.get('sources', [])
            raw_layers = anim_data.get('layers', [])
            self.layer_source_lookup = {
                layer.get('id', idx): layer for idx, layer in enumerate(raw_layers)
            }
            
            self.log_widget.log(f"Loading animation: {anim_data['name']}", "INFO")
            self.current_animation_name = anim_data.get('name')
            
            json_dir = os.path.dirname(self.current_json_path) if self.current_json_path else None
            self.gl_widget.texture_atlases = self._load_texture_atlases_for_sources(
                sources,
                json_dir=json_dir,
                use_cache=False
            )
            self._rebuild_source_atlas_lookup(sources, self.gl_widget.texture_atlases)
            
            # Parse animation data
            blend_version = self.current_blend_version or 1
            animation = self._build_animation_struct(
                anim_data,
                blend_version,
                self.current_json_path,
                resource_dict=self.current_json_data
            )
            layers = animation.layers

            self.canonical_layer_names = set()
            # Ensure costume metadata (and any inferred clone aliases) are cached
            # before we attempt to seed canonical clones.
            self._refresh_costume_list()
            if previous_costume_key:
                preserved_costume_entry = self.costume_entry_map.get(previous_costume_key)
            self._apply_canonical_clones_to_base(layers)
            self._record_layer_defaults(layers)
            self._apply_cached_layer_visibility(layers)
            self.base_layer_cache = self._clone_layers(layers)
            self._configure_costume_shaders(None, None)

            self.gl_widget.player.load_animation(animation)
            self.gl_widget.set_layer_atlas_overrides({})
            self.gl_widget.set_layer_pivot_context({})
            self._reset_costume_runtime_state(animation.layers)
            self.base_texture_atlases = list(self.gl_widget.texture_atlases)
            self.costume_atlas_cache.clear()
            self.update_layer_panel()
            self.selected_layer_ids.clear()
            self.primary_selected_layer_id = None
            self.selection_lock_enabled = False
            self.layer_panel.set_selection_state(self.selected_layer_ids)
            self.apply_selection_state()
            self.control_panel.set_pose_controls_enabled(True)
            self.control_panel.set_sprite_tools_enabled(bool(layers))
            self._reset_edit_history()
            self.update_timeline()
            if self.current_animation_name:
                self.load_audio_for_animation(self.current_animation_name)
            self._configure_animation_bpm()

            # Reinitialize GL to load textures
            self.gl_widget.makeCurrent()
            self.gl_widget.initializeGL()
            self._restore_sprite_workshop_edits()
            self.gl_widget.doneCurrent()

            self.log_widget.log(f"Animation loaded successfully with {len(layers)} layers", "SUCCESS")
            self.gl_widget.set_anchor_logging_enabled(self.anchor_debug_enabled)
            if self.anchor_debug_enabled:
                # Schedule an anchor debug dump after the first frame to capture pivot math
                QTimer.singleShot(500, lambda: self._dump_anchor_debug())

            if preserved_costume_entry:
                self.log_widget.log(
                    f"Reapplying costume '{preserved_costume_entry.display_name}' to animation '{self.current_animation_name}'",
                    "INFO"
                )
                self._restore_costume_selection(preserved_costume_entry.key)
                self._apply_costume_to_animation(preserved_costume_entry)
            else:
                self._restore_costume_selection(None)
            self._refresh_timeline_keyframes()

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(tb)
            self.log_widget.log(f"Error loading animation: {e}", "ERROR")
            self.log_widget.log(tb, "ERROR")
        finally:
            self._stop_hang_watchdog()

    def _dump_anchor_debug(self, attempt: int = 0):
        """Attempt to dump renderer anchor logs; retry briefly if empty."""
        if not self.anchor_debug_enabled:
            return
        try:
            renderer = getattr(self.gl_widget, "renderer", None)
            if renderer is None:
                return
            if renderer.log_data:
                renderer.write_log_to_file("anchor_debug.txt")
                return
            if attempt >= 4:
                renderer.write_log_to_file("anchor_debug.txt")
                return
            QTimer.singleShot(500, lambda: self._dump_anchor_debug(attempt + 1))
        except Exception:
            return

    def load_audio_for_animation(self, animation_name: str):
        """Load and sync the audio clip that matches the selected animation."""
        if not animation_name or not self.game_path:
            self.current_audio_path = None
            self.audio_manager.clear()
            self.control_panel.update_audio_status("Audio: not available", False)
            return
        self._start_hang_watchdog("load_audio")
        try:
            audio_path = self._find_audio_for_animation(animation_name)

            if not audio_path:
                self.current_audio_path = None
                self.audio_manager.clear()
                self.control_panel.update_audio_status(f"{animation_name}: missing", False)
                self.log_widget.log(f"No audio clip found for animation '{animation_name}'", "WARNING")
                return

            self.current_audio_path = audio_path
            if self.audio_manager.load_file(audio_path):
                current_time = self.gl_widget.player.current_time
                self._update_audio_speed()
                if self.gl_widget.player.playing:
                    self.audio_manager.play(current_time)
                else:
                    self.audio_manager.seek(current_time)
                rel_path = os.path.relpath(audio_path, os.path.join(self.game_path, "data"))
                self.control_panel.update_audio_status(f"{animation_name} -> {rel_path}", True)
                self.log_widget.log(f"Loaded audio clip: {rel_path}", "SUCCESS")
            else:
                self.audio_manager.clear()
                self.control_panel.update_audio_status("Audio: failed to load", False)
                self.log_widget.log(f"Failed to load audio file: {audio_path}", "ERROR")
        finally:
            self._stop_hang_watchdog()

    def _find_audio_for_animation(self, animation_name: str) -> Optional[str]:
        """Return an absolute path to the audio clip for a given animation name."""
        if not self.game_path or not animation_name:
            return None

        buddy_override = self._lookup_buddy_audio(animation_name)
        if buddy_override:
            return buddy_override

        music_dir = os.path.join(self.game_path, "data", "audio", "music")
        if not os.path.exists(music_dir):
            return None

        monster_token = self._current_monster_token()
        raw_candidates = self._build_audio_name_candidates(
            animation_name,
            monster_token=monster_token
        )

        extensions = ['.ogg', '.wav', '.mp3']
        for candidate in raw_candidates:
            base = os.path.splitext(candidate)[0]
            for ext in extensions:
                check_path = os.path.join(music_dir, base + ext)
                if os.path.exists(check_path):
                    return check_path

        if not self.audio_library:
            return None

        normalized_keys: List[str] = []
        for candidate in raw_candidates:
            normalized = self._normalize_audio_key(candidate)
            if normalized:
                normalized_keys.extend(self._expand_audio_key_variants(normalized))

        seen_keys: Set[str] = set()
        for key in normalized_keys:
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            paths = self.audio_library.get(key)
            if paths:
                return paths[0]

        if self._should_attempt_fuzzy_audio(animation_name):
            fallback_path = self._fuzzy_audio_library_lookup(
                normalized_keys,
                monster_token=monster_token,
                animation_name=animation_name
            )
            if fallback_path:
                return fallback_path
        return None

    @staticmethod
    def _normalize_audio_key(value: str) -> str:
        """Normalize strings so they can be matched against music filenames."""
        if not value:
            return ""
        base = os.path.splitext(os.path.basename(value))[0]
        base = base.replace('-', '_').replace(' ', '_').lower()
        base = re.sub(r'[^0-9a-z_]+', '', base)
        base = re.sub(r'_+', '_', base)
        return base.strip('_')

    def _build_audio_name_candidates(
        self,
        animation_name: str,
        *,
        monster_token: Optional[str] = None
    ) -> List[str]:
        """
        Build a list of plausible filename bases for a given animation name.
        This yields the raw values we try before normalizing them.
        """
        candidates: List[str] = []
        seen: Set[str] = set()

        def add(value: str):
            value = value.strip()
            if value and value not in seen:
                seen.add(value)
                candidates.append(value)

        normalized_path = animation_name.replace("\\", "/").strip()
        add(normalized_path)

        parts = [segment for segment in normalized_path.split("/") if segment]
        for part in reversed(parts):
            add(part)

        if len(parts) >= 2:
            add(f"{parts[-2]}_{parts[-1]}")

        if monster_token:
            token_clean = monster_token.strip()
            add(token_clean)
            add(f"monster_{token_clean}")
            add(f"{token_clean}_monster")
            if parts:
                add(f"{token_clean}_{parts[-1]}")
                add(f"{parts[-1]}_{token_clean}")
        snapshot = list(candidates)
        for value in snapshot:
            stripped = re.sub(r'^[0-9]+[_-]*', '', value)
            if stripped:
                add(stripped)
            trimmed_suffix = re.sub(r'[_-]*[0-9]+$', '', value)
            if trimmed_suffix:
                add(trimmed_suffix)
            # Some assets prefix the filename with an extra digit (e.g., 117- vs 17-).
            if value and value[0].isdigit():
                add(f"1{value}")
            if monster_token:
                add(f"{monster_token}_{value}")
                add(f"{value}_{monster_token}")

        return candidates

    def _expand_audio_key_variants(self, base_key: str) -> List[str]:
        """
        Expand a normalized key into additional variants by removing numeric prefixes,
        suffixes, and common descriptors. This helps match inconsistent naming.
        """
        variants: List[str] = []
        synonym_map = {
            'min': ['minor'],
            'maj': ['major'],
        }

        def add(value: str):
            value = value.strip('_')
            if value and value not in variants:
                variants.append(value)
                add_synonym_variants(value)

        def add_synonym_variants(value: str):
            tokens_local = [token for token in value.split('_') if token]
            if not tokens_local:
                return
            for idx, token in enumerate(tokens_local):
                if token in synonym_map:
                    for alt in synonym_map[token]:
                        new_tokens = list(tokens_local)
                        new_tokens[idx] = alt
                        add('_'.join(new_tokens))

        add(base_key)
        if not base_key:
            return variants

        tokens = [token for token in base_key.split('_') if token]
        if not tokens:
            return variants

        # Remove numeric prefixes
        prefix_tokens = tokens[:]
        while prefix_tokens and prefix_tokens[0].isdigit():
            prefix_tokens = prefix_tokens[1:]
            if prefix_tokens:
                add('_'.join(prefix_tokens))

        # Remove numeric suffixes
        suffix_tokens = tokens[:]
        while suffix_tokens and suffix_tokens[-1].isdigit():
            suffix_tokens = suffix_tokens[:-1]
            if suffix_tokens:
                add('_'.join(suffix_tokens))

        # Remove rarity descriptors at the front
        descriptor_prefix = tokens[:]
        while descriptor_prefix and descriptor_prefix[0] in {'common', 'rare', 'epic'}:
            descriptor_prefix = descriptor_prefix[1:]
            if descriptor_prefix:
                add('_'.join(descriptor_prefix))

        # Remove descriptors at the end (loop, intro, idle, song)
        descriptor_suffix = tokens[:]
        while descriptor_suffix and descriptor_suffix[-1] in {'loop', 'intro', 'idle', 'song'}:
            descriptor_suffix = descriptor_suffix[:-1]
            if descriptor_suffix:
                add('_'.join(descriptor_suffix))

        # Add each individual token and pairs for broader matching
        for token in tokens:
            add(token)

        if len(tokens) >= 2:
            for idx in range(len(tokens) - 1):
                pair = '_'.join(tokens[idx:idx + 2])
                add(pair)

        return variants

    def _lookup_buddy_audio(self, animation_name: str) -> Optional[str]:
        """Return an audio path resolved via the buddy manifests, if available."""
        if not animation_name:
            return None
        direct = self.buddy_audio_tracks.get(animation_name)
        if direct:
            return direct
        normalized = self._normalize_audio_key(animation_name)
        if normalized:
            return self.buddy_audio_tracks_normalized.get(normalized)
        return None

    def _should_attempt_fuzzy_audio(self, animation_name: str) -> bool:
        """
        Determine whether fuzzy audio matching should be attempted for a particular
        animation. Idle/dance/pose style animations typically have no audio, so we
        avoid auto-matching clips for them.
        """
        normalized = self._normalize_audio_key(animation_name)
        tokens = set(self._audio_key_tokens(normalized))
        if not tokens:
            return True

        allow_tokens = {
            "song", "sing", "singer", "verse", "chorus", "vox", "vocal",
            "music", "melody", "lead", "track", "performance"
        }
        block_tokens = {
            "idle", "idle1", "idle2", "dance", "idleloop", "pose", "breath",
            "blink", "walk", "pace", "cam", "camera", "intro", "outro",
            "celebrate", "gesture", "sleep", "stand", "rest", "sit", "hype",
            "emote", "store", "shop", "market"
        }

        if tokens & allow_tokens:
            return True
        if tokens & block_tokens:
            return False
        return True

    def _fuzzy_audio_library_lookup(
        self,
        normalized_keys: List[str],
        *,
        monster_token: Optional[str],
        animation_name: str
    ) -> Optional[str]:
        """
        Attempt to match an animation's audio using relaxed token comparisons so
        we can handle clips whose filenames only loosely resemble the animation id.
        """
        if not self.audio_library:
            return None

        candidate_entries: List[Tuple[str, Set[str]]] = []
        seen_candidates: Set[str] = set()
        for key in normalized_keys:
            if not key or key in seen_candidates:
                continue
            seen_candidates.add(key)
            tokens = set(self._audio_key_tokens(key))
            if tokens:
                candidate_entries.append((key, tokens))
        if not candidate_entries:
            return None

        monster_tokens: Set[str] = set()
        if monster_token:
            normalized_monster = self._normalize_audio_key(monster_token)
            monster_tokens = set(self._audio_key_tokens(normalized_monster))

        best_score = 0.0
        best_path: Optional[str] = None
        best_key: Optional[str] = None
        token_cache: Dict[str, Set[str]] = {}

        for lib_key, paths in self.audio_library.items():
            if not paths:
                continue
            cached = token_cache.get(lib_key)
            if cached is None:
                cached = set(self._audio_key_tokens(lib_key))
                token_cache[lib_key] = cached
            if not cached:
                continue

            for candidate_key, candidate_tokens in candidate_entries:
                overlap = cached & candidate_tokens
                meaningful_overlap = [tok for tok in overlap if not tok.isdigit()]
                if not meaningful_overlap:
                    continue
                overlap_score = sum(self._audio_token_weight(tok) for tok in overlap)
                similarity = difflib.SequenceMatcher(None, candidate_key, lib_key).ratio()
                score = overlap_score + (similarity * 0.75)

                if candidate_tokens <= cached:
                    score += 0.3
                if cached <= candidate_tokens:
                    score += 0.2
                if monster_tokens:
                    if monster_tokens <= cached:
                        score += 0.4
                    elif monster_tokens & cached:
                        score += 0.2
                if len(overlap) >= 2:
                    score += 0.25 * (len(overlap) - 1)

                if score > best_score:
                    best_score = score
                    best_path = paths[0]
                    best_key = lib_key

        if best_path and best_score >= 1.75:
            rel_path = best_path
            if self.game_path:
                try:
                    rel_path = os.path.relpath(
                        best_path,
                        os.path.join(self.game_path, "data")
                    )
                except ValueError:
                    rel_path = best_path
            self.log_widget.log(
                f"Audio fallback matched '{animation_name}' -> '{rel_path}' via fuzzy tokens (score {best_score:.2f})",
                "INFO"
            )
            return best_path
        return None

    @staticmethod
    def _audio_key_tokens(value: Optional[str]) -> List[str]:
        if not value:
            return []
        return [token for token in value.split('_') if token]

    @staticmethod
    def _audio_token_weight(token: str) -> float:
        if not token:
            return 0.0
        lowered = token.lower()
        if lowered.isdigit():
            return 0.05
        low_signal = {
            "monster", "song", "loop", "intro", "outro", "idle", "verse",
            "chorus", "vox", "vocal", "voice", "mix", "stem", "track",
            "main", "alt", "part", "rare", "epic", "common"
        }
        if lowered in low_signal:
            return 0.2
        if len(lowered) == 1:
            return 0.35
        return 1.0
    
    def update_layer_panel(self):
        """Update the layer visibility panel"""
        animation = self.gl_widget.player.animation
        self._reset_layer_thumbnail_cache()
        if animation:
            self.layer_panel.set_default_hidden_layers(self._default_hidden_layer_ids)
            self.layer_panel.update_layers(animation.layers)
            variant_layers = self._detect_layers_with_sprite_variants(animation.layers)
            self.layer_panel.set_layers_with_sprite_variants(variant_layers)
            self.layer_panel.set_selection_state(self.selected_layer_ids)
            self._refresh_layer_thumbnails()
        else:
            self.layer_panel.set_default_hidden_layers(set())
            self.layer_panel.update_layers([])
            self.layer_panel.set_layers_with_sprite_variants(set())
            self.layer_panel.set_selection_state(set())

    def _reset_layer_thumbnail_cache(self):
        """Clear cached sprite previews so rows rebuild cleanly."""
        self._layer_thumbnail_cache.clear()
        self._atlas_image_cache.clear()
        self._layer_sprite_preview_state.clear()
        if hasattr(self, "layer_panel") and self.layer_panel:
            self.layer_panel.clear_layer_thumbnails()

    # --- Sprite workshop helpers -------------------------------------------------

    def _atlas_cache_key(self, atlas: TextureAtlas) -> Optional[str]:
        """Return a stable identifier for an atlas image."""
        path = getattr(atlas, "image_path", None)
        if path:
            return os.path.normcase(os.path.abspath(path))
        source = getattr(atlas, "source_name", None)
        if source:
            return source.lower()
        return None

    def _atlas_display_name(self, atlas: TextureAtlas) -> str:
        """Friendly label for an atlas."""
        if getattr(atlas, "source_name", None):
            return atlas.source_name
        path = getattr(atlas, "image_path", None)
        if path:
            return os.path.basename(path)
        return f"Atlas {id(atlas)}"

    def _sprite_workshop_key(self, atlas: TextureAtlas, sprite_name: str) -> Tuple[str, str]:
        """Return a dict key for sprite replacement bookkeeping."""
        atlas_key = self._atlas_cache_key(atlas) or f"atlas_{id(atlas)}"
        return (atlas_key, sprite_name.lower())

    def _ensure_mutable_atlas_bitmap(self, atlas: TextureAtlas) -> Optional[Image.Image]:
        """Return a mutable PIL image for an atlas, cloning the source if needed."""
        key = self._atlas_cache_key(atlas)
        if not key:
            return None
        image = self._atlas_modified_images.get(key)
        if image is not None:
            return image
        base = self._load_atlas_image(atlas)
        if base is None:
            return None
        mutable = base.copy()
        self._atlas_modified_images[key] = mutable
        return mutable

    def _original_atlas_bitmap(self, atlas: TextureAtlas) -> Optional[Image.Image]:
        """Return the pristine atlas bitmap saved when it was first loaded."""
        key = self._atlas_cache_key(atlas)
        if not key:
            return None
        original = self._atlas_original_image_cache.get(key)
        if original is not None:
            return original
        active = self._load_atlas_image(atlas)
        if active is None:
            return None
        backup = active.copy()
        self._atlas_original_image_cache[key] = backup
        return backup

    def _extract_sprite_bitmap(self, atlas: TextureAtlas, sprite: SpriteInfo) -> Optional[Image.Image]:
        """Return a PIL image for a sprite, un-rotated for editing."""
        atlas_image = self._load_atlas_image(atlas)
        if not atlas_image:
            return None
        box = (
            int(sprite.x),
            int(sprite.y),
            int(sprite.x + sprite.w),
            int(sprite.y + sprite.h),
        )
        cropped = atlas_image.crop(box)
        if sprite.rotated:
            cropped = cropped.rotate(90, expand=True)
        return cropped

    def _coerce_patch_dimensions(
        self,
        image: Image.Image,
        expected_size: Tuple[int, int],
        sprite_name: str,
        tolerance: int = 2,
    ) -> Tuple[Optional[Image.Image], Optional[str]]:
        """Return a copy sized to the expected dimensions within a pixel tolerance."""
        width, height = image.size
        target_w, target_h = expected_size
        if width == target_w and height == target_h:
            return image, None
        within_tolerance = (
            abs(width - target_w) <= tolerance
            and abs(height - target_h) <= tolerance
        )
        if within_tolerance:
            canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
            crop_box = (0, 0, min(width, target_w), min(height, target_h))
            canvas.paste(image.crop(crop_box), (0, 0))
            return canvas, None
        return None, (
            f"Sprite '{sprite_name}' expects {target_w}x{target_h} pixels (±{tolerance}), "
            f"but got {width}x{height}."
        )

    def _prepare_patch_for_sprite(
        self,
        sprite: SpriteInfo,
        edited_image: Image.Image,
    ) -> Tuple[Optional[Image.Image], Optional[str]]:
        """Return an atlas-oriented patch for a sprite edit, or an error message."""
        image = edited_image.convert("RGBA")
        expected_w = int(sprite.w)
        expected_h = int(sprite.h)
        tolerance_px = 2
        if sprite.rotated:
            expected_input = (int(sprite.h), int(sprite.w))
            normalized, error = self._coerce_patch_dimensions(
                image,
                expected_input,
                sprite.name,
                tolerance=tolerance_px,
            )
            if error:
                return None, error
            patch = normalized.rotate(-90, expand=True)
        else:
            normalized, error = self._coerce_patch_dimensions(
                image,
                (expected_w, expected_h),
                sprite.name,
                tolerance=tolerance_px,
            )
            if error:
                return None, error
            patch = normalized
        patch = patch.crop((0, 0, expected_w, expected_h))
        return patch, None

    def _upload_sprite_patch(self, atlas: TextureAtlas, sprite: SpriteInfo, patch: Image.Image):
        """Upload a sprite region patch to the GPU texture."""
        if not patch:
            return
        if not atlas.texture_id:
            self.gl_widget.makeCurrent()
            try:
                atlas.load_texture()
            finally:
                self.gl_widget.doneCurrent()
        if not atlas.texture_id:
            return
        arr = np.array(patch, dtype=np.float32) / 255.0
        alpha = arr[..., 3:4]
        arr[..., :3] *= alpha
        arr = (arr * 255.0).astype(np.uint8)
        self.gl_widget.makeCurrent()
        try:
            glBindTexture(GL_TEXTURE_2D, atlas.texture_id)
            glTexSubImage2D(
                GL_TEXTURE_2D,
                0,
                int(sprite.x),
                int(sprite.y),
                patch.width,
                patch.height,
                GL_RGBA,
                GL_UNSIGNED_BYTE,
                arr.tobytes(),
            )
        finally:
            self.gl_widget.doneCurrent()

    def _apply_sprite_patch(
        self,
        atlas: TextureAtlas,
        sprite: SpriteInfo,
        patch: Image.Image,
        source_path: Optional[str] = None,
    ) -> bool:
        """Paste a prepared patch into the atlas bitmap and upload it."""
        atlas_bitmap = self._ensure_mutable_atlas_bitmap(atlas)
        if atlas_bitmap is None or patch is None:
            return False
        atlas_bitmap.paste(patch, (int(sprite.x), int(sprite.y)))
        self._upload_sprite_patch(atlas, sprite, patch)
        key = self._sprite_workshop_key(atlas, sprite.name)
        atlas_key = key[0]
        self._atlas_dirty_flags[atlas_key] = True
        self._sprite_replacements[key] = SpriteReplacementRecord(
            atlas_key=atlas_key,
            sprite_name=sprite.name,
            source_path=os.path.abspath(source_path) if source_path else None,
            applied_at=datetime.now().isoformat(timespec="seconds"),
        )
        self._reset_layer_thumbnail_cache()
        self.gl_widget.update()
        return True

    def replace_sprite_from_file(
        self,
        atlas: TextureAtlas,
        sprite: SpriteInfo,
        file_path: str,
    ) -> Tuple[bool, str]:
        """Replace a sprite region with pixels loaded from disk."""
        try:
            edited = Image.open(file_path).convert("RGBA")
        except Exception as exc:
            return False, f"Failed to load image: {exc}"
        patch, error = self._prepare_patch_for_sprite(sprite, edited)
        if error:
            return False, error
        if not self._apply_sprite_patch(atlas, sprite, patch, source_path=file_path):
            return False, "Unable to update atlas texture."
        self.log_widget.log(
            f"Sprite '{sprite.name}' updated using '{os.path.basename(file_path)}'.",
            "SUCCESS",
        )
        return True, ""

    def remove_sprite_replacement(self, atlas: TextureAtlas, sprite: SpriteInfo) -> bool:
        """Restore a sprite region to its original pixels."""
        key = self._sprite_workshop_key(atlas, sprite.name)
        if key not in self._sprite_replacements:
            return False
        original = self._original_atlas_bitmap(atlas)
        target = self._ensure_mutable_atlas_bitmap(atlas)
        if original is None or target is None:
            return False
        region = (
            int(sprite.x),
            int(sprite.y),
            int(sprite.x + sprite.w),
            int(sprite.y + sprite.h),
        )
        patch = original.crop(region)
        target.paste(patch, (region[0], region[1]))
        self._upload_sprite_patch(atlas, sprite, patch)
        del self._sprite_replacements[key]
        atlas_key = key[0]
        still_dirty = any(k[0] == atlas_key for k in self._sprite_replacements.keys())
        if not still_dirty:
            self._atlas_dirty_flags.pop(atlas_key, None)
        self._reset_layer_thumbnail_cache()
        self.gl_widget.update()
        self.log_widget.log(
            f"Sprite '{sprite.name}' restored to atlas defaults.",
            "INFO",
        )
        return True

    def is_sprite_modified(self, atlas: TextureAtlas, sprite_name: str) -> bool:
        """Return True if a sprite currently has an override applied."""
        key = self._sprite_workshop_key(atlas, sprite_name)
        return key in self._sprite_replacements

    def sprite_preview_pixmap(self, atlas: TextureAtlas, sprite: SpriteInfo, max_edge: int = 256) -> Optional[QPixmap]:
        """Return a scaled pixmap preview for workshop UI."""
        image = self._extract_sprite_bitmap(atlas, sprite)
        if not image:
            return None
        pixmap = self._pil_image_to_qpixmap(image)
        if pixmap is None:
            return None
        if max(pixmap.width(), pixmap.height()) > max_edge:
            pixmap = pixmap.scaled(
                max_edge,
                max_edge,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        return pixmap

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """Return a filesystem-safe version of a sprite or atlas label."""
        safe = re.sub(r"[^0-9a-zA-Z_.-]+", "_", name or "").strip("_")
        return safe or "sprite"

    def export_sprite_segments(
        self,
        atlas: TextureAtlas,
        sprite_names: List[str],
        destination: str,
    ) -> Tuple[bool, str]:
        """Export selected sprites as standalone PNGs plus a manifest."""
        if not sprite_names:
            sprite_names = sorted(atlas.sprites.keys())
        if not sprite_names:
            return False, "Atlas has no sprites to export."
        atlas_label = self._atlas_display_name(atlas)
        atlas_dir = os.path.join(destination, self._sanitize_filename(atlas_label))
        os.makedirs(atlas_dir, exist_ok=True)
        manifest_entries: List[Dict[str, Any]] = []
        exported = 0
        for name in sprite_names:
            sprite = atlas.sprites.get(name)
            if not sprite:
                continue
            image = self._extract_sprite_bitmap(atlas, sprite)
            if not image:
                continue
            filename = f"{self._sanitize_filename(sprite.name)}.png"
            export_path = os.path.join(atlas_dir, filename)
            image.save(export_path, "PNG")
            exported += 1
            manifest_entries.append(
                {
                    "name": sprite.name,
                    "file": filename,
                    "size": [image.width, image.height],
                    "atlas_region": {
                        "x": int(sprite.x),
                        "y": int(sprite.y),
                        "w": int(sprite.w),
                        "h": int(sprite.h),
                        "rotated": bool(sprite.rotated),
                    },
                    "offset": [sprite.offset_x, sprite.offset_y],
                    "original_size": [sprite.original_w, sprite.original_h],
                    "pivot": [sprite.pivot_x, sprite.pivot_y],
                }
            )
        if exported == 0:
            return False, "No sprites could be exported."
        manifest = {
            "atlas": atlas_label,
            "image_size": [atlas.image_width, atlas.image_height],
            "sprites": manifest_entries,
        }
        manifest_path = os.path.join(atlas_dir, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)
        self.log_widget.log(
            f"Exported {exported} sprite{'s' if exported != 1 else ''} from {atlas_label} to {atlas_dir}.",
            "SUCCESS",
        )
        return True, atlas_dir

    def _build_atlas_xml_tree(self, atlas: TextureAtlas, image_name: str) -> ET.ElementTree:
        """Return an ElementTree representing the atlas layout."""
        root = ET.Element(
            "TextureAtlas",
            {
                "imagePath": image_name,
                "width": str(atlas.image_width),
                "height": str(atlas.image_height),
            },
        )
        if atlas.is_hires:
            root.set("hires", "true")
        for sprite in sorted(atlas.sprites.values(), key=lambda info: info.name.lower()):
            elem = ET.SubElement(
                root,
                "sprite",
                {
                    "n": sprite.name,
                    "x": str(int(sprite.x)),
                    "y": str(int(sprite.y)),
                    "w": str(int(sprite.w)),
                    "h": str(int(sprite.h)),
                    "pX": f"{float(sprite.pivot_x):.6f}",
                    "pY": f"{float(sprite.pivot_y):.6f}",
                    "oX": f"{float(sprite.offset_x):.6f}",
                    "oY": f"{float(sprite.offset_y):.6f}",
                    "oW": f"{float(sprite.original_w):.6f}",
                    "oH": f"{float(sprite.original_h):.6f}",
                },
            )
            if sprite.rotated:
                elem.set("r", "y")
            if sprite.vertices:
                verts = " ".join(f"{x:.6f} {y:.6f}" for x, y in sprite.vertices)
                ET.SubElement(elem, "vertices").text = verts
            if sprite.vertices_uv:
                # Convert normalized UV data back to pixel coordinates.
                uv_pairs = []
                for u, v in sprite.vertices_uv:
                    uv_pairs.append(f"{u * atlas.image_width:.6f} {v * atlas.image_height:.6f}")
                ET.SubElement(elem, "verticesUV").text = " ".join(uv_pairs)
            if sprite.triangles:
                ET.SubElement(elem, "triangles").text = " ".join(str(idx) for idx in sprite.triangles)
        return ET.ElementTree(root)

    def export_modified_spritesheet(self, atlas: TextureAtlas, output_png: str) -> Tuple[bool, str]:
        """Write the current atlas bitmap plus an updated XML manifest."""
        os.makedirs(os.path.dirname(output_png) or ".", exist_ok=True)
        key = self._atlas_cache_key(atlas)
        atlas_image = self._atlas_modified_images.get(key) or self._load_atlas_image(atlas)
        if atlas_image is None:
            return False, "Unable to load atlas pixels."
        atlas_image.save(output_png, "PNG")
        xml_output = os.path.splitext(output_png)[0] + ".xml"
        existing_xml = getattr(atlas, "xml_path", None)
        xml_tree: Optional[ET.ElementTree] = None
        if existing_xml and os.path.exists(existing_xml):
            try:
                xml_tree = ET.parse(existing_xml)
                xml_root = xml_tree.getroot()
                xml_root.set("imagePath", os.path.basename(output_png))
                xml_root.set("width", str(atlas.image_width))
                xml_root.set("height", str(atlas.image_height))
                if atlas.is_hires:
                    xml_root.set("hires", "true")
            except Exception:
                xml_tree = None
        if xml_tree is None:
            xml_tree = self._build_atlas_xml_tree(atlas, os.path.basename(output_png))
        xml_tree.write(xml_output, encoding="utf-8", xml_declaration=True)
        self.log_widget.log(
            f"Exported spritesheet '{self._atlas_display_name(atlas)}' to {output_png} and {xml_output}.",
            "SUCCESS",
        )
        return True, xml_output

    def import_spritesheet_into_atlas(
        self,
        atlas: TextureAtlas,
        image_path: str,
        xml_path: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Apply a spritesheet + XML manifest onto an existing atlas."""
        if not atlas:
            return False, "No atlas is currently active."
        if not image_path or not os.path.exists(image_path):
            return False, "Spritesheet image could not be found."
        inferred_xml = xml_path or os.path.splitext(image_path)[0] + ".xml"
        if not os.path.exists(inferred_xml):
            return False, "Matching spritesheet XML could not be found."

        import_atlas = TextureAtlas()
        atlas_root = os.path.dirname(image_path) or "."
        if not import_atlas.load_from_xml(inferred_xml, atlas_root):
            return False, "Failed to parse spritesheet XML."

        try:
            with Image.open(import_atlas.image_path) as raw_sheet:
                sheet_image = raw_sheet.convert("RGBA")
        except Exception as exc:
            return False, f"Failed to open spritesheet image: {exc}"

        imported = 0
        skipped: List[str] = []
        for sprite_name, source_sprite in import_atlas.sprites.items():
            target_sprite = atlas.sprites.get(sprite_name)
            if not target_sprite:
                continue
            region = (
                int(source_sprite.x),
                int(source_sprite.y),
                int(source_sprite.x + source_sprite.w),
                int(source_sprite.y + source_sprite.h),
            )
            try:
                patch = sheet_image.crop(region)
            except Exception:
                skipped.append(sprite_name)
                continue
            expected_size = (int(target_sprite.w), int(target_sprite.h))
            if patch.size != expected_size:
                skipped.append(sprite_name)
                continue
            if not self._apply_sprite_patch(atlas, target_sprite, patch, source_path=image_path):
                skipped.append(sprite_name)
                continue
            imported += 1

        if imported == 0:
            return False, "No matching sprites from the spritesheet could be imported."

        if skipped:
            self.log_widget.log(
                f"Imported {imported} sprite(s) from {os.path.basename(image_path)} "
                f"(skipped {len(skipped)} mismatch(es)).",
                "WARNING",
            )
        else:
            self.log_widget.log(
                f"Imported {imported} sprite(s) from {os.path.basename(image_path)}.",
                "SUCCESS",
            )
        return True, ""

    def get_sprite_workshop_entries(self) -> List[Dict[str, Any]]:
        """Return a deduplicated list of atlases that can be edited."""
        entries: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for atlas in self._iter_active_atlases():
            key = self._atlas_cache_key(atlas) or f"atlas_{id(atlas)}"
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                {
                    "key": key,
                    "label": self._atlas_display_name(atlas),
                    "atlas": atlas,
                    "sprite_count": len(atlas.sprites),
                    "modified": sum(
                        1 for sprite_key in self._sprite_replacements.keys() if sprite_key[0] == key
                    ),
                }
            )
        return entries

    def list_sprites_for_atlas(self, atlas: TextureAtlas) -> List[SpriteInfo]:
        """Return sprites sorted alphabetically for UI display."""
        return sorted(atlas.sprites.values(), key=lambda sprite: sprite.name.lower())

    def show_sprite_workshop(self):
        """Display the Sprite Workshop dialog."""
        if not list(self._iter_active_atlases()):
            QMessageBox.information(
                self,
                "Sprite Workshop",
                "Load an animation first so its sprites can be customized.",
            )
            return
        if not self._sprite_workshop_dialog:
            self._sprite_workshop_dialog = SpriteWorkshopDialog(self)
        self._sprite_workshop_dialog.refresh_entries()
        self._sprite_workshop_dialog.show()
        self._sprite_workshop_dialog.raise_()
        self._sprite_workshop_dialog.activateWindow()

    def _apply_solid_bg_color(self, rgba: Tuple[int, int, int, int], *, announce: bool):
        """Persist the active export background color."""
        r = max(0, min(255, int(rgba[0])))
        g = max(0, min(255, int(rgba[1])))
        b = max(0, min(255, int(rgba[2])))
        a = max(0, min(255, int(rgba[3])))
        self.solid_bg_color = (r, g, b, a)
        self.settings.setValue('export/solid_bg_color', self._rgba_to_hex(self.solid_bg_color))
        if announce:
            self.log_widget.log(
                f"Background color set to {self._rgba_to_hex(self.solid_bg_color)}.",
                "SUCCESS",
            )

    @staticmethod
    def _rgba_to_hex(rgba: Tuple[int, int, int, int]) -> str:
        r, g, b, a = rgba
        return f"#{r:02X}{g:02X}{b:02X}{a:02X}"

    @staticmethod
    def _parse_rgba_hex(value: str, fallback: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
        text = (value or "").strip().lstrip("#")
        if len(text) == 6:
            text += "FF"
        if len(text) != 8:
            return fallback
        try:
            r = int(text[0:2], 16)
            g = int(text[2:4], 16)
            b = int(text[4:6], 16)
            a = int(text[6:8], 16)
            return (r, g, b, a)
        except ValueError:
            return fallback

    def _active_background_color(self) -> Optional[Tuple[int, int, int, int]]:
        """Return the currently configured export background color, if enabled."""
        if getattr(self, "solid_bg_enabled", False):
            return getattr(self, "solid_bg_color", (0, 0, 0, 255))
        return None

    def _suggest_unused_background_color(self) -> Optional[Tuple[int, int, int, int]]:
        """Return a color that does not appear in the active atlases, if possible."""
        atlas_arrays: List[np.ndarray] = []
        for atlas in self._iter_active_atlases():
            image = self._load_atlas_image(atlas)
            if image is None:
                continue
            try:
                atlas_arrays.append(np.asarray(image, dtype=np.uint8))
            except Exception:
                continue
        if not atlas_arrays:
            return (255, 0, 255, 255)

        def color_exists(rgb: Tuple[int, int, int]) -> bool:
            target = np.array(rgb, dtype=np.uint8)
            for arr in atlas_arrays:
                if arr.ndim < 3 or arr.shape[2] < 3:
                    continue
                if np.any(np.all(arr[..., :3] == target, axis=-1)):
                    return True
            return False

        preferred_colors = [
            (255, 0, 255),
            (0, 255, 0),
            (0, 255, 255),
            (255, 255, 0),
            (0, 128, 255),
            (255, 128, 0),
            (0, 255, 180),
            (255, 0, 180),
        ]
        for rgb in preferred_colors:
            if not color_exists(rgb):
                return (rgb[0], rgb[1], rgb[2], 255)

        rng = random.Random()
        rng.seed(len(atlas_arrays))
        for _ in range(96):
            rgb = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
            if not color_exists(rgb):
                return (rgb[0], rgb[1], rgb[2], 255)
        return None

    def _refresh_layer_thumbnails(self):
        """Update per-layer sprite thumbnails based on the current time."""
        if not hasattr(self, "layer_panel") or not self.layer_panel:
            return
        animation = getattr(self.gl_widget.player, "animation", None)
        if not animation:
            self.layer_panel.clear_layer_thumbnails()
            self._layer_sprite_preview_state.clear()
            return
        current_time = self.gl_widget.player.current_time
        for layer in animation.layers:
            if layer.layer_id is None:
                continue
            sprite_name = ""
            try:
                state = self.gl_widget.player.get_layer_state(layer, current_time)
                sprite_name = state.get("sprite_name") or ""
            except Exception:
                sprite_name = ""
            previous = self._layer_sprite_preview_state.get(layer.layer_id)
            if previous == sprite_name:
                continue
            pixmap = self._get_layer_thumbnail_pixmap(sprite_name)
            self.layer_panel.set_layer_thumbnail(layer.layer_id, pixmap)
            self._layer_sprite_preview_state[layer.layer_id] = sprite_name

    def _get_layer_thumbnail_pixmap(self, sprite_name: Optional[str]) -> Optional[QPixmap]:
        """Return a cached pixmap for a sprite, loading it if necessary."""
        if not sprite_name:
            return None
        if sprite_name in self._layer_thumbnail_cache:
            return self._layer_thumbnail_cache[sprite_name]
        resolved = self._resolve_sprite_asset(sprite_name)
        if not resolved:
            self._layer_thumbnail_cache[sprite_name] = None
            return None
        sprite, atlas = resolved
        atlas_image = self._load_atlas_image(atlas)
        if atlas_image is None:
            self._layer_thumbnail_cache[sprite_name] = None
            return None
        crop_box = (sprite.x, sprite.y, sprite.x + sprite.w, sprite.y + sprite.h)
        try:
            sprite_image = atlas_image.crop(crop_box)
        except Exception:
            self._layer_thumbnail_cache[sprite_name] = None
            return None
        if getattr(sprite, "rotated", False):
            sprite_image = sprite_image.rotate(90, expand=True)
        pixmap = self._pil_image_to_qpixmap(sprite_image)
        self._layer_thumbnail_cache[sprite_name] = pixmap
        return pixmap

    def _pil_image_to_qpixmap(self, image: Optional[Image.Image]) -> Optional[QPixmap]:
        """Convert a PIL Image into a QPixmap without relying on ImageQt (Pillow 10+ compatibility)."""
        if image is None:
            return None
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        width, height = image.size
        if width == 0 or height == 0:
            return None
        try:
            buffer = image.tobytes("raw", "BGRA")
            qimage = QImage(buffer, width, height, QImage.Format.Format_ARGB32)
            return QPixmap.fromImage(qimage.copy())
        except Exception:
            return None

    def _load_atlas_image(self, atlas: TextureAtlas) -> Optional[Image.Image]:
        """Load and cache the atlas image backing a sprite."""
        path = getattr(atlas, "image_path", None)
        if not path:
            return None
        key = os.path.normcase(os.path.abspath(path))
        modified = self._atlas_modified_images.get(key)
        if modified is not None:
            return modified
        cached = self._atlas_image_cache.get(key)
        if cached is not None:
            return cached
        try:
            atlas_image = Image.open(path)
            atlas_image = atlas_image.convert("RGBA")
            atlas_image.load()
        except Exception as exc:
            self._atlas_image_cache[key] = None
            self.log_widget.log(
                f"Failed to load atlas preview '{os.path.basename(path)}': {exc}",
                "WARNING",
            )
            return None
        if key not in self._atlas_original_image_cache:
            self._atlas_original_image_cache[key] = atlas_image.copy()
        self._atlas_image_cache[key] = atlas_image
        return atlas_image

    def _resolve_sprite_asset(
        self, sprite_name: str
    ) -> Optional[Tuple[Any, TextureAtlas]]:
        """Locate the sprite/atlas pair for a given sprite name."""
        for atlas in self._iter_active_atlases():
            sprite = atlas.get_sprite(sprite_name)
            if sprite:
                return sprite, atlas
        return None

    def _iter_active_atlases(self):
        """Yield every atlas currently assigned to the renderer."""
        seen: Set[int] = set()
        for atlas in getattr(self.gl_widget, "texture_atlases", []) or []:
            if atlas and id(atlas) not in seen:
                seen.add(id(atlas))
                yield atlas
        overrides = getattr(self.gl_widget, "layer_atlas_overrides", {}) or {}
        for chain in overrides.values():
            if not chain:
                continue
            for atlas in chain:
                if atlas and id(atlas) not in seen:
                    seen.add(id(atlas))
                    yield atlas

    def _restore_sprite_workshop_edits(self) -> None:
        """Reupload Sprite Workshop overrides after atlases are rebuilt."""
        if not self._sprite_replacements:
            return
        atlas_map: Dict[str, List[TextureAtlas]] = {}
        for atlas in self._iter_active_atlases():
            key = self._atlas_cache_key(atlas)
            if key:
                atlas_map.setdefault(key, []).append(atlas)
        if not atlas_map:
            return
        for (atlas_key, sprite_name) in list(self._sprite_replacements.keys()):
            patched_bitmap = self._atlas_modified_images.get(atlas_key)
            if patched_bitmap is None:
                continue
            atlases = atlas_map.get(atlas_key)
            if not atlases:
                continue
            for atlas in atlases:
                sprite = atlas.sprites.get(sprite_name)
                if not sprite:
                    continue
                region = (
                    int(sprite.x),
                    int(sprite.y),
                    int(sprite.x + sprite.w),
                    int(sprite.y + sprite.h),
                )
                try:
                    patch = patched_bitmap.crop(region)
                except Exception:
                    continue
                self._upload_sprite_patch(atlas, sprite, patch)

    # ------------------------------------------------------------------ #
    # Sprite assignment helpers
    # ------------------------------------------------------------------ #

    def _atlas_for_layer(self, layer: LayerData) -> Optional[TextureAtlas]:
        """Return the atlas associated with a layer."""
        source_entry = self.layer_source_lookup.get(layer.layer_id)
        if source_entry:
            src_key = source_entry.get("src")
            if src_key in self.source_atlas_lookup:
                return self.source_atlas_lookup[src_key]
            if isinstance(src_key, str):
                atlas = self.source_atlas_lookup.get(src_key.lower())
                if atlas:
                    return atlas
        for keyframe in layer.keyframes:
            name = keyframe.sprite_name
            if not name:
                continue
            _, atlas = self._find_sprite_in_atlases(name)
            if atlas:
                return atlas
        return None

    def _collect_available_sprites_for_layers(self, layers: List[LayerData]) -> List[str]:
        """Return a sorted list of sprite names available for the provided layers."""
        sprites: Set[str] = set()
        for layer in layers:
            atlas = self._atlas_for_layer(layer)
            if not atlas:
                continue
            sprites.update(atlas.sprites.keys())
        return sorted(sprites, key=lambda value: value.lower())

    def _detect_layers_with_sprite_variants(self, layers: List[LayerData]) -> Set[int]:
        """Return layer ids whose keyframes already swap between multiple sprites."""
        variant_ids: Set[int] = set()
        for layer in layers:
            if layer.layer_id is None:
                continue
            sprite_names = {
                frame.sprite_name
                for frame in layer.keyframes
                if frame.sprite_name
            }
            if len(sprite_names) > 1:
                variant_ids.add(layer.layer_id)
        return variant_ids

    def _gather_keyframes_for_times(
        self,
        layers: List[LayerData],
        times: List[float],
    ) -> List[Tuple[LayerData, KeyframeData]]:
        """Return keyframes from layers that fall on the provided timestamps."""
        tolerance = self._marker_time_tolerance()
        matches: List[Tuple[LayerData, KeyframeData]] = []
        seen: Set[Tuple[int, float]] = set()
        for layer in layers:
            for frame in layer.keyframes:
                for target in times:
                    if abs(frame.time - target) <= tolerance:
                        key = (layer.layer_id, frame.time)
                        if key in seen:
                            continue
                        seen.add(key)
                        matches.append((layer, frame))
                        break
        return matches

    def assign_sprite_to_keyframes(self, layer_ids: Optional[List[int]] = None):
        """Assign a sprite name to the selected keyframes."""
        animation = getattr(self.gl_widget.player, "animation", None)
        if not animation:
            self.log_widget.log("Load an animation before assigning sprites.", "WARNING")
            return

        explicit_layers = layer_ids is not None
        if explicit_layers:
            requested_ids = {
                layer_id for layer_id in (layer_ids or [])
                if layer_id is not None
            }
        elif self.selected_layer_ids:
            requested_ids = set(self.selected_layer_ids)
        else:
            requested_ids = set()

        if requested_ids:
            target_layers = [
                layer for layer in animation.layers
                if layer.layer_id in requested_ids
            ]
            if explicit_layers and not target_layers:
                self.log_widget.log("Selected layer is unavailable for sprite assignment.", "WARNING")
                return
        else:
            target_layers = list(animation.layers)

        if not target_layers:
            self.log_widget.log("Select at least one layer to assign sprites.", "INFO")
            return
        selected_times = sorted(self._selected_marker_times)
        implicit_time = False
        if selected_times:
            target_times = selected_times
        else:
            target_times = [float(self.gl_widget.player.current_time)]
            implicit_time = True
        matches = self._gather_keyframes_for_times(target_layers, target_times)
        if not matches:
            if implicit_time:
                self.log_widget.log(
                    "No keyframes found at the current time. Select keyframe markers on the timeline first.",
                    "INFO",
                )
            else:
                self.log_widget.log("No keyframes matched the selected markers.", "INFO")
            return
        unique_layers: List[LayerData] = []
        seen_layers: Set[int] = set()
        for layer, _frame in matches:
            if layer.layer_id in seen_layers:
                continue
            seen_layers.add(layer.layer_id)
            unique_layers.append(layer)
        sprite_options = self._collect_available_sprites_for_layers(unique_layers)
        if not sprite_options:
            self.log_widget.log(
                "Could not locate any sprites for the selected layers' atlases.",
                "WARNING",
            )
            return
        atlas_labels: Set[str] = set()
        for layer in unique_layers:
            atlas = self._atlas_for_layer(layer)
            if not atlas:
                continue
            label = getattr(atlas, "source_name", None)
            if label:
                atlas_labels.add(label)
        description = None
        if atlas_labels:
            if len(atlas_labels) == 1:
                only = next(iter(atlas_labels))
                description = f"Sprites from {only} ({len(sprite_options)} available)."
            else:
                joined = ", ".join(sorted(atlas_labels))
                description = f"Sprites from {joined} ({len(sprite_options)} available)."
        sprite_entries: List[Tuple[str, Optional[QPixmap]]] = []
        for name in sprite_options:
            pixmap = self._get_layer_thumbnail_pixmap(name)
            sprite_entries.append((name, pixmap))
        current_sprite = next((frame.sprite_name for _, frame in matches if frame.sprite_name), None)
        picker = SpritePickerDialog(
            sprite_entries,
            current_sprite=current_sprite,
            description=description,
            parent=self,
        )
        if picker.exec() != QDialog.DialogCode.Accepted:
            return
        sprite_name = picker.selected_sprite()
        if not sprite_name:
            return
        layer_ids = sorted({layer.layer_id for layer, _ in matches if layer.layer_id is not None})
        if not layer_ids:
            self.log_widget.log("Unable to determine which layers to update.", "ERROR")
            return
        self._begin_keyframe_action(layer_ids)
        touched_layers: Set[int] = set()
        for layer, frame in matches:
            if frame.sprite_name == sprite_name:
                continue
            frame.sprite_name = sprite_name
            if frame.immediate_sprite == 0:
                frame.immediate_sprite = 1
            touched_layers.add(layer.layer_id)
        for layer_id in touched_layers:
            synced_layer = self.gl_widget.get_layer_by_id(layer_id)
            if synced_layer:
                self._sync_layer_source_frames(synced_layer)
        self._finalize_keyframe_action("assign_sprite")
        if touched_layers:
            self.gl_widget.update()
            self._refresh_layer_thumbnails()
            variant_layers = self._detect_layers_with_sprite_variants(animation.layers)
            self.layer_panel.set_layers_with_sprite_variants(variant_layers)
            self.log_widget.log(
                f"Assigned sprite '{sprite_name}' to {len(matches)} keyframe(s).",
                "SUCCESS",
            )
        else:
            self.log_widget.log("The selected keyframes already use that sprite.", "INFO")

    def toggle_layer_visibility(self, layer: LayerData, state: int):
        """Toggle layer visibility"""
        layer.visible = (state == Qt.CheckState.Checked.value)
        self._remember_layer_visibility(layer)
        self.gl_widget.update()

    def reset_layer_visibility_to_default(self):
        """Restore all layer visibilities to their recorded defaults."""
        animation = self.gl_widget.player.animation
        if not animation or not self._default_layer_visibility:
            return
        cache_key = self._current_json_cache_key()
        if cache_key and cache_key in self.layer_visibility_cache:
            self.layer_visibility_cache.pop(cache_key, None)
        for layer in animation.layers:
            if layer.layer_id in self._default_layer_visibility:
                layer.visible = self._default_layer_visibility[layer.layer_id]
        self.layer_panel.set_default_hidden_layers(self._default_hidden_layer_ids)
        self.layer_panel.update_layers(animation.layers)
        variant_layers = self._detect_layers_with_sprite_variants(animation.layers)
        self.layer_panel.set_layers_with_sprite_variants(variant_layers)
        self.layer_panel.set_selection_state(self.selected_layer_ids)
        self._reset_layer_thumbnail_cache()
        self._refresh_layer_thumbnails()
        self.gl_widget.update()

    def reset_layer_order_to_default(self):
        """Restore layer ordering to the recorded default order."""
        animation = self.gl_widget.player.animation
        if not animation or not self._default_layer_order:
            return
        id_to_layer = {layer.layer_id: layer for layer in animation.layers}
        new_layers: List[LayerData] = []
        for layer_id in self._default_layer_order:
            layer = id_to_layer.get(layer_id)
            if layer:
                new_layers.append(layer)
        for layer_id, layer in id_to_layer.items():
            if layer_id not in self._default_layer_order:
                new_layers.append(layer)
        if not new_layers:
            return
        animation.layers = new_layers
        self.layer_panel.update_layers(animation.layers)
        variant_layers = self._detect_layers_with_sprite_variants(animation.layers)
        self.layer_panel.set_layers_with_sprite_variants(variant_layers)
        self.layer_panel.set_selection_state(self.selected_layer_ids)
        self._reset_layer_thumbnail_cache()
        self._refresh_layer_thumbnails()
        self.gl_widget.update()

    def on_layer_order_changed(self, ordered_ids: List[int]):
        """Reorder animation layers to match the drag/drop order from the UI."""
        animation = self.gl_widget.player.animation
        if not animation:
            return
        current_layers = animation.layers or []
        if len(ordered_ids) != len(current_layers):
            return
        id_to_layer = {layer.layer_id: layer for layer in current_layers}
        try:
            new_layers = [id_to_layer[layer_id] for layer_id in ordered_ids]
        except KeyError:
            return
        if new_layers == current_layers:
            return
        animation.layers = new_layers
        self.layer_panel.update_layers(animation.layers)
        variant_layers = self._detect_layers_with_sprite_variants(animation.layers)
        self.layer_panel.set_layers_with_sprite_variants(variant_layers)
        self.layer_panel.set_selection_state(self.selected_layer_ids)
        self._reset_layer_thumbnail_cache()
        self._refresh_layer_thumbnails()
        self.gl_widget.update()
    
    def update_timeline(self):
        """Update timeline slider range"""
        if self.gl_widget.player.animation:
            duration = self.gl_widget.player.duration
            slider_max = max(1, int(duration * 1000))
            self.timeline.set_slider_maximum(slider_max)
            self.timeline.set_time_label(f"{self.gl_widget.player.current_time:.2f} / {duration:.2f}s")
            self.timeline.set_current_time(self.gl_widget.player.current_time)
            self._refresh_timeline_keyframes()
        else:
            self.timeline.set_slider_maximum(1)
            self.timeline.set_time_label("0.00 / 0.00s")
            self.timeline.set_current_time(0.0)
            self.timeline.set_keyframe_markers([], 0.0)

    def _refresh_timeline_keyframes(self):
        """Update timeline markers to reflect current keyframes."""
        animation = getattr(self.gl_widget.player, "animation", None)
        if not animation:
            self.timeline.set_keyframe_markers([], 0.0)
            return
        duration = max(0.0, self.gl_widget.player.duration)
        if self.selected_layer_ids:
            target_ids = set(self.selected_layer_ids)
        else:
            target_ids = {layer.layer_id for layer in animation.layers}
        markers: Set[float] = set()
        for layer in animation.layers:
            if layer.layer_id not in target_ids:
                continue
            for keyframe in layer.keyframes:
                markers.add(max(0.0, float(keyframe.time)))
        marker_list = sorted(markers)
        self.timeline.set_keyframe_markers(marker_list, duration)
        self._sync_marker_selection(marker_list)

    def _marker_time_tolerance(self) -> float:
        return 1.0 / 600.0

    def _sync_marker_selection(self, available_markers: List[float]):
        if not hasattr(self, "timeline"):
            return
        tolerance = self._marker_time_tolerance()
        retained: List[float] = []
        for selected in sorted(self._selected_marker_times):
            match = next((marker for marker in available_markers if abs(marker - selected) <= tolerance), None)
            if match is not None and not any(abs(match - existing) <= tolerance for existing in retained):
                retained.append(match)
        self._selected_marker_times = set(retained)
        self.timeline.set_marker_selection(retained)

    def _replace_marker_selection(self, times: List[float]):
        normalized: List[float] = []
        tolerance = self._marker_time_tolerance()
        for time in sorted(times):
            clamped = max(0.0, float(time))
            if normalized and abs(clamped - normalized[-1]) <= tolerance:
                continue
            normalized.append(clamped)
        self._selected_marker_times = set(normalized)
        if hasattr(self, "timeline"):
            self.timeline.set_marker_selection(normalized)

    def _remove_marker_selection_times(self, times: List[float]):
        if not self._selected_marker_times:
            return
        tolerance = self._marker_time_tolerance()
        removal = [max(0.0, float(value)) for value in times]
        remaining: List[float] = []
        for existing in sorted(self._selected_marker_times):
            if any(abs(existing - target) <= tolerance for target in removal):
                continue
            remaining.append(existing)
        self._selected_marker_times = set(remaining)
        if hasattr(self, "timeline"):
            self.timeline.set_marker_selection(remaining)
    
    def toggle_playback(self):
        """Toggle animation playback"""
        self.gl_widget.player.playing = not self.gl_widget.player.playing
        is_playing = self.gl_widget.player.playing
        self.timeline.set_play_button_text("Pause" if is_playing else "Play")
        self._sync_audio_playback(is_playing)
    
    def toggle_loop(self, state: int):
        """Toggle animation looping"""
        self.gl_widget.player.loop = (state == Qt.CheckState.Checked.value)
    
    def on_timeline_changed(self, value: int):
        """Handle timeline slider change"""
        if not self.gl_widget.player.animation:
            return

        time = value / 1000.0
        self.gl_widget.set_time(time)

        if self.audio_manager.is_ready:
            self.audio_manager.seek(time)

        duration = self.gl_widget.player.duration
        self.timeline.set_time_label(f"{time:.2f} / {duration:.2f}s")
        self.timeline.set_current_time(time)
        self._refresh_layer_thumbnails()

    def on_timeline_slider_pressed(self):
        """Mark that the user is scrubbing the timeline."""
        self._timeline_user_scrubbing = True
        if self.gl_widget.player.playing and self.audio_manager.is_ready:
            self._resume_audio_after_scrub = True
            self.audio_manager.pause()
        else:
            self._resume_audio_after_scrub = False

    def on_timeline_slider_released(self):
        """Resume playback if the user was scrubbing."""
        self._timeline_user_scrubbing = False
        if self._resume_audio_after_scrub and self.audio_manager.is_ready:
            self.audio_manager.play(self.gl_widget.player.current_time)
        self._resume_audio_after_scrub = False

    def on_keyframe_marker_clicked(self, time_value: float):
        """Jump to a keyframe marker when the user clicks the marker bar."""
        if not self.gl_widget.player.animation:
            return
        duration = max(0.0, self.gl_widget.player.duration)
        clamped = max(0.0, min(time_value, duration))
        slider = self.timeline.timeline_slider
        slider.blockSignals(True)
        slider.setValue(int(clamped * 1000))
        slider.blockSignals(False)
        self.gl_widget.set_time(clamped)
        if self.audio_manager.is_ready:
            self.audio_manager.seek(clamped)
        self.timeline.set_time_label(f"{clamped:.2f} / {duration:.2f}s")
        self.timeline.set_current_time(clamped)
        self._refresh_layer_thumbnails()

    def on_keyframe_marker_remove_requested(self, time_values: List[float]):
        """Remove keyframes shared at the given time."""
        animation = self.gl_widget.player.animation
        if not animation:
            return
        sanitized = sorted({max(0.0, float(value)) for value in (time_values or [])})
        if not sanitized:
            return
        if self.selected_layer_ids:
            target_ids = sorted(self.selected_layer_ids)
        else:
            target_ids = [layer.layer_id for layer in animation.layers]
        if not target_ids:
            return
        self._begin_keyframe_action(target_ids)
        removed = 0
        tolerance = self._marker_time_tolerance()
        for layer in animation.layers:
            if layer.layer_id not in target_ids:
                continue
            original_count = len(layer.keyframes)
            kept_frames = [
                frame for frame in layer.keyframes
                if all(abs(frame.time - value) > tolerance for value in sanitized)
            ]
            if len(kept_frames) != original_count:
                layer.keyframes = kept_frames
                removed += original_count - len(kept_frames)
                self._sync_layer_source_frames(layer)
        self._finalize_keyframe_action("delete_keyframe")
        if removed:
            self.gl_widget.player.calculate_duration()
            self.update_timeline()
            self.gl_widget.update()
            self.log_widget.log(f"Removed {removed} keyframe(s).", "SUCCESS")
            self._remove_marker_selection_times(sanitized)
        else:
            self.log_widget.log("No keyframes found at the selected time to remove.", "INFO")

    def on_keyframe_marker_dragged(self, original_times: List[float], delta: float):
        """Move selected keyframes to a new timestamp by dragging markers."""
        animation = self.gl_widget.player.animation
        if not animation:
            return
        if not original_times:
            return
        if abs(delta) < 1e-6:
            return
        if self.selected_layer_ids:
            target_ids = sorted(self.selected_layer_ids)
        else:
            target_ids = [layer.layer_id for layer in animation.layers]
        if not target_ids:
            return
        self._begin_keyframe_action(target_ids)
        tolerance = self._marker_time_tolerance()
        duration = max(0.0, self.gl_widget.player.duration)
        pairs: List[Tuple[float, float]] = []
        for value in original_times:
            old_time = max(0.0, float(value))
            proposed = old_time + float(delta)
            new_time = min(max(proposed, 0.0), duration)
            pairs.append((old_time, new_time))
        moved = 0
        for layer in animation.layers:
            if layer.layer_id not in target_ids:
                continue
            updated = False
            for frame in layer.keyframes:
                for old_time, target_time in pairs:
                    if abs(frame.time - old_time) <= tolerance:
                        frame.time = target_time
                        updated = True
            if updated:
                layer.keyframes.sort(key=lambda frame: frame.time)
                self._sync_layer_source_frames(layer)
                moved += 1
        self._finalize_keyframe_action("move_keyframe")
        if moved:
            self.gl_widget.player.calculate_duration()
            self.update_timeline()
            self.gl_widget.update()
            self._replace_marker_selection([pair[1] for pair in pairs])
            self.log_widget.log(
                f"Moved {len(pairs)} keyframe time(s) by {delta:.3f}s", "SUCCESS"
            )
        else:
            self.log_widget.log("No keyframes moved for the selected layers.", "INFO")

    def on_keyframe_selection_changed(self, selected_times: List[float]):
        """Store the current marker selection from the timeline widget."""
        normalized: List[float] = []
        tolerance = self._marker_time_tolerance()
        for value in sorted(selected_times):
            clamped = max(0.0, float(value))
            if normalized and abs(clamped - normalized[-1]) <= tolerance:
                continue
            normalized.append(clamped)
        self._selected_marker_times = set(normalized)

    def copy_selected_keyframes(self):
        """Copy keyframes anchored at the currently selected marker times."""
        animation = self.gl_widget.player.animation
        if not animation:
            self.log_widget.log("Load an animation before copying keyframes.", "WARNING")
            return
        selected_times = sorted(self._selected_marker_times)
        if not selected_times:
            self.log_widget.log("Select keyframes in the timeline before copying.", "INFO")
            return
        tolerance = self._marker_time_tolerance()
        if self.selected_layer_ids:
            target_ids = {layer_id for layer_id in self.selected_layer_ids}
        else:
            target_ids = {layer.layer_id for layer in animation.layers if layer.layer_id is not None}
        if not target_ids:
            self.log_widget.log("No layers available to copy keyframes from.", "WARNING")
            return
        base_time = selected_times[0]
        clipboard_layers: List[Dict[str, Any]] = []
        total_frames = 0
        for layer in animation.layers:
            if layer.layer_id not in target_ids:
                continue
            matches: List[Dict[str, Any]] = []
            for frame in layer.keyframes:
                if any(abs(frame.time - marker) <= tolerance for marker in selected_times):
                    matches.append(
                        {
                            "time_offset": float(frame.time - base_time),
                            "data": replace(frame),
                        }
                    )
            if matches:
                clipboard_layers.append(
                    {
                        "layer_name": layer.name,
                        "layer_id": layer.layer_id,
                        "keyframes": matches,
                    }
                )
                total_frames += len(matches)
        if not clipboard_layers:
            self.log_widget.log("No keyframes matched the current selection to copy.", "INFO")
            return
        self._keyframe_clipboard = {
            "layers": clipboard_layers,
            "copied_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.log_widget.log(
            f"Copied {total_frames} keyframe(s) from {len(clipboard_layers)} layer(s).",
            "SUCCESS",
        )

    def paste_copied_keyframes(self):
        """Paste keyframes from the clipboard at the current timeline position."""
        animation = self.gl_widget.player.animation
        if not animation:
            self.log_widget.log("Load an animation before pasting keyframes.", "WARNING")
            return
        if not self._keyframe_clipboard:
            self.log_widget.log("Copy keyframes before attempting to paste.", "INFO")
            return
        clipboard_layers = self._keyframe_clipboard.get("layers") or []
        if not clipboard_layers:
            self.log_widget.log("Clipboard is empty; copy keyframes before pasting.", "INFO")
            return
        layer_lookup = {layer.name.lower(): layer for layer in animation.layers if layer.name}
        candidate_layers: Dict[int, LayerData] = {}
        total_candidate_frames = 0
        for entry in clipboard_layers:
            frames = entry.get("keyframes") or []
            if not frames:
                continue
            layer_name = (entry.get("layer_name") or "").lower()
            target_layer = layer_lookup.get(layer_name)
            if not target_layer or target_layer.layer_id is None:
                continue
            candidate_layers[target_layer.layer_id] = target_layer
            total_candidate_frames += len(frames)
        if not candidate_layers or total_candidate_frames == 0:
            self.log_widget.log(
                "Copied keyframes do not match any layers in this animation.",
                "WARNING",
            )
            return
        self._begin_keyframe_action(list(candidate_layers.keys()))
        inserted = 0
        new_marker_times: List[float] = []
        target_start = max(0.0, float(self.gl_widget.player.current_time))
        for entry in clipboard_layers:
            frames = entry.get("keyframes") or []
            if not frames:
                continue
            layer_name = (entry.get("layer_name") or "").lower()
            target_layer = layer_lookup.get(layer_name)
            if not target_layer or target_layer.layer_id is None:
                continue
            for payload in frames:
                frame_copy: KeyframeData = replace(payload.get("data"))
                offset = float(payload.get("time_offset", 0.0))
                frame_copy.time = max(0.0, target_start + offset)
                target_layer.keyframes.append(frame_copy)
                inserted += 1
                new_marker_times.append(frame_copy.time)
            target_layer.keyframes.sort(key=lambda frame: frame.time)
            self._sync_layer_source_frames(target_layer)
        if inserted == 0:
            self._pending_keyframe_action = None
            self._update_keyframe_history_controls()
            self.log_widget.log("Pasting failed because no keyframes could be inserted.", "WARNING")
            return
        self._finalize_keyframe_action("paste_keyframes")
        self.gl_widget.player.calculate_duration()
        self.update_timeline()
        self.gl_widget.update()
        self._refresh_timeline_keyframes()
        if new_marker_times:
            self._replace_marker_selection(new_marker_times)
        self.log_widget.log(
            f"Pasted {inserted} keyframe(s) into {len(candidate_layers)} layer(s).",
            "SUCCESS",
        )

    def on_animation_time_changed(self, current: float, duration: float):
        """Update the timeline UI when the renderer advances."""
        if not self.gl_widget.player.animation:
            return
        if not self._timeline_user_scrubbing:
            slider = self.timeline.timeline_slider
            slider.blockSignals(True)
            slider.setValue(int(current * 1000))
            slider.blockSignals(False)
        duration = duration if duration > 0 else self.gl_widget.player.duration
        self.timeline.set_time_label(f"{current:.2f} / {duration:.2f}s")
        self.timeline.set_current_time(current)
        if not self.gl_widget.player.playing and not self._timeline_user_scrubbing:
            self._refresh_layer_thumbnails()

    def on_animation_looped(self):
        """Keep audio aligned with animation loops."""
        if self.audio_manager.is_ready and self.gl_widget.player.playing:
            self.audio_manager.restart()

    def on_playback_state_changed(self, playing: bool):
        """Handle automatic playback state changes (e.g., reaching the end)."""
        self.timeline.set_play_button_text("Pause" if playing else "Play")
        if not playing and self.audio_manager.is_ready:
            self.audio_manager.pause()

    # ------------------------------------------------------------------ #
    # Pose recording helpers
    # ------------------------------------------------------------------ #

    def on_pose_influence_changed(self, mode: Optional[str]) -> None:
        """Update how recorded poses propagate to future keyframes."""
        if mode not in {"current", "forward"}:
            mode = "current"
        self.pose_influence_mode = mode

    def on_record_pose_clicked(self) -> None:
        """Bake the current gizmo offsets into animation data."""
        animation = self.gl_widget.player.animation
        if not animation:
            self.log_widget.log("Load an animation before recording poses.", "WARNING")
            return
        if not self.selected_layer_ids:
            self.log_widget.log("Select at least one layer to record a pose.", "WARNING")
            return

        layer_ids = set(self.selected_layer_ids)
        if not layer_ids:
            return
        base_states_map, final_states_map = self._gather_pose_state_maps(layer_ids)
        time_value = round(self.gl_widget.player.current_time, 5)
        influence = self.pose_influence_mode or "current"
        applied = 0
        self._begin_keyframe_action(list(layer_ids))
        for layer_id in layer_ids:
            if self._record_pose_for_layer(
                layer_id,
                time_value,
                influence,
                base_states_map,
                final_states_map,
                force=True,
            ):
                applied += 1

        if not applied:
            self.log_widget.log("No gizmo offsets detected; nothing to record.", "INFO")
            self._finalize_keyframe_action("record_pose")
            return

        self._clear_user_offsets_for_layers(self.selected_layer_ids)
        self.update_offset_display()
        self.gl_widget.player.calculate_duration()
        self.update_timeline()
        self.gl_widget.update()
        scope = "propagated" if influence == "forward" else "local"
        self._finalize_keyframe_action("record_pose")
        self.log_widget.log(
            f"Recorded pose for {applied} layer(s) ({scope}).",
            "SUCCESS"
        )

    def on_reset_pose_clicked(self) -> None:
        """Reset selected keyframes back to their baseline values."""
        animation = self.gl_widget.player.animation
        if not animation:
            self.log_widget.log("Load an animation before resetting poses.", "WARNING")
            return
        if not self.selected_layer_ids:
            self.log_widget.log("Select at least one layer to reset.", "WARNING")
            return
        if not self._pose_baseline_player:
            self.log_widget.log("Baseline unavailable; reload the animation to reset keyframes.", "WARNING")
            return
        time_value = round(self.gl_widget.player.current_time, 5)
        influence = self.pose_influence_mode or "current"
        layer_ids = sorted(self.selected_layer_ids)
        self._begin_keyframe_action(layer_ids)
        applied = 0
        for layer_id in layer_ids:
            if self._reset_pose_for_layer(layer_id, time_value, influence):
                applied += 1
        self._finalize_keyframe_action("reset_pose")
        if not applied:
            self.log_widget.log("No matching keyframes found to reset.", "INFO")
            return
        self.gl_widget.player.calculate_duration()
        self.update_timeline()
        self.gl_widget.update()
        scope = "propagated" if influence == "forward" else "local"
        self.log_widget.log(
            f"Reset {applied} keyframe(s) to defaults ({scope}).",
            "SUCCESS"
        )

    def _record_pose_for_layer(
        self,
        layer_id: int,
        time_value: float,
        influence: str,
        base_states: Dict[int, Dict[str, Any]],
        final_states: Dict[int, Dict[str, Any]],
        tolerance: float = 1e-4,
        force: bool = False
    ) -> bool:
        """Capture gizmo offsets for a single layer."""
        layer = self.gl_widget.get_layer_by_id(layer_id)
        if not layer:
            return False
        anchor_override = self.gl_widget.layer_anchor_overrides.get(layer_id)
        anchor_captured = False
        local_state = self.gl_widget.player.get_layer_state(layer, time_value)
        base_state = base_states.get(layer_id, {})
        final_state = final_states.get(layer_id, {})
        if not base_state or not final_state:
            base_state = {}
            final_state = {}
        base_pos_x = float(local_state.get("pos_x", 0.0))
        base_pos_y = float(local_state.get("pos_y", 0.0))
        base_rot = float(local_state.get("rotation", 0.0))
        base_scale_x = float(local_state.get("scale_x", 100.0))
        base_scale_y = float(local_state.get("scale_y", 100.0))

        offset_x, offset_y = self.gl_widget.layer_offsets.get(layer_id, (0.0, 0.0))
        rot_offset = self.gl_widget.layer_rotations.get(layer_id, 0.0)
        scale_offset_x, scale_offset_y = self.gl_widget.layer_scale_offsets.get(layer_id, (1.0, 1.0))

        base_anchor_x = float(base_state.get("anchor_world_x", base_state.get("tx", 0.0)))
        base_anchor_y = float(base_state.get("anchor_world_y", base_state.get("ty", 0.0)))
        final_anchor_x = float(final_state.get("anchor_world_x", base_anchor_x))
        final_anchor_y = float(final_state.get("anchor_world_y", base_anchor_y))
        world_delta_x = (final_anchor_x - base_anchor_x) + offset_x
        world_delta_y = (final_anchor_y - base_anchor_y) + offset_y
        has_translation = (
            abs(world_delta_x) > tolerance or abs(world_delta_y) > tolerance
        )
        has_rotation = abs(rot_offset) > tolerance
        has_scale = (
            abs(scale_offset_x - 1.0) > tolerance
            or abs(scale_offset_y - 1.0) > tolerance
        )

        if anchor_override is not None:
            anchor_captured = self._update_layer_anchor(layer, anchor_override)

        changes_requested = has_translation or has_rotation or has_scale or anchor_captured
        if not changes_requested and not force:
            return False

        local_delta_x = 0.0
        local_delta_y = 0.0
        if changes_requested:
            local_delta_x, local_delta_y = self._world_delta_to_local(
                layer,
                base_states,
                world_delta_x,
                world_delta_y,
            )
        target_pos_x = base_pos_x + local_delta_x
        target_pos_y = base_pos_y + local_delta_y
        target_rot = base_rot + (rot_offset if has_rotation else 0.0)
        target_scale_x = base_scale_x * (scale_offset_x if has_scale else 1.0)
        target_scale_y = base_scale_y * (scale_offset_y if has_scale else 1.0)

        eval_state = self.gl_widget.player.get_layer_state(layer, self.gl_widget.player.current_time)
        keyframe = self._find_keyframe_at_time(layer, time_value)
        created = False
        if not keyframe:
            keyframe = KeyframeData(time=time_value)
            layer.keyframes.append(keyframe)
            created = True
            keyframe.pos_x = base_pos_x
            keyframe.pos_y = base_pos_y
            keyframe.scale_x = base_scale_x
            keyframe.scale_y = base_scale_y
            keyframe.rotation = base_rot
            keyframe.opacity = float(eval_state.get("opacity", keyframe.opacity))
            snapshot_sprite = eval_state.get("sprite_name")
            if snapshot_sprite:
                keyframe.sprite_name = snapshot_sprite
            keyframe.immediate_sprite = -1
            keyframe.r = int(eval_state.get("r", keyframe.r))
            keyframe.g = int(eval_state.get("g", keyframe.g))
            keyframe.b = int(eval_state.get("b", keyframe.b))

        keyframe.time = time_value
        if has_translation:
            keyframe.pos_x = target_pos_x
            keyframe.pos_y = target_pos_y
            if created:
                keyframe.immediate_pos = 0
        if has_rotation:
            keyframe.rotation = target_rot
            if created:
                keyframe.immediate_rotation = 0
        if has_scale:
            keyframe.scale_x = target_scale_x
            keyframe.scale_y = target_scale_y
            if created:
                keyframe.immediate_scale = 0

        if created:
            layer.keyframes.sort(key=lambda frame: frame.time)

        if influence == "forward" and changes_requested:
            delta_x = local_delta_x if has_translation else 0.0
            delta_y = local_delta_y if has_translation else 0.0
            delta_rot = target_rot - base_rot if has_rotation else 0.0
            factor_x = scale_offset_x if has_scale else 1.0
            factor_y = scale_offset_y if has_scale else 1.0
            forward_tol = 1.0 / 600.0
            for frame in layer.keyframes:
                if frame.time <= time_value + forward_tol:
                    continue
                if has_translation:
                    frame.pos_x += delta_x
                    frame.pos_y += delta_y
                if has_rotation:
                    frame.rotation += delta_rot
                if has_scale:
                    frame.scale_x *= factor_x
                    frame.scale_y *= factor_y

        self._sync_layer_source_frames(layer)
        return True

    def _reset_pose_for_layer(
        self,
        layer_id: int,
        time_value: float,
        influence: str,
        tolerance: float = 1e-4
    ) -> bool:
        """Reset an individual layer's keyframe to its baseline state."""
        layer = self.gl_widget.get_layer_by_id(layer_id)
        if not layer:
            return False
        keyframe = self._find_keyframe_at_time(layer, time_value)
        if not keyframe:
            return False
        baseline_state = self._get_pose_baseline_state(layer_id, time_value)
        if not baseline_state:
            return False

        target_pos_x = float(baseline_state.get("pos_x", keyframe.pos_x))
        target_pos_y = float(baseline_state.get("pos_y", keyframe.pos_y))
        target_rot = float(baseline_state.get("rotation", keyframe.rotation))
        target_scale_x = float(baseline_state.get("scale_x", keyframe.scale_x))
        target_scale_y = float(baseline_state.get("scale_y", keyframe.scale_y))

        current_pos_x = float(keyframe.pos_x)
        current_pos_y = float(keyframe.pos_y)
        current_rot = float(keyframe.rotation)
        current_scale_x = float(keyframe.scale_x)
        current_scale_y = float(keyframe.scale_y)

        changed = False
        delta_pos_x = current_pos_x - target_pos_x
        delta_pos_y = current_pos_y - target_pos_y
        delta_rot = current_rot - target_rot
        factor_x = (current_scale_x / target_scale_x) if abs(target_scale_x) > tolerance else 1.0
        factor_y = (current_scale_y / target_scale_y) if abs(target_scale_y) > tolerance else 1.0

        if abs(delta_pos_x) > tolerance or abs(delta_pos_y) > tolerance:
            keyframe.pos_x = target_pos_x
            keyframe.pos_y = target_pos_y
            changed = True
        else:
            delta_pos_x = 0.0
            delta_pos_y = 0.0
        if abs(delta_rot) > tolerance:
            keyframe.rotation = target_rot
            changed = True
        else:
            delta_rot = 0.0
        if abs(current_scale_x - target_scale_x) > tolerance or abs(current_scale_y - target_scale_y) > tolerance:
            keyframe.scale_x = target_scale_x
            keyframe.scale_y = target_scale_y
            changed = True
        else:
            factor_x = 1.0
            factor_y = 1.0

        if not changed:
            return False

        keyframe.time = time_value
        layer.keyframes.sort(key=lambda frame: frame.time)

        if influence == "forward":
            forward_tol = 1.0 / 600.0
            for frame in layer.keyframes:
                if frame.time <= time_value + forward_tol:
                    continue
                if delta_pos_x or delta_pos_y:
                    frame.pos_x -= delta_pos_x
                    frame.pos_y -= delta_pos_y
                if delta_rot:
                    frame.rotation -= delta_rot
                if abs(factor_x - 1.0) > tolerance:
                    frame.scale_x = frame.scale_x / factor_x if abs(factor_x) > tolerance else frame.scale_x
                if abs(factor_y - 1.0) > tolerance:
                    frame.scale_y = frame.scale_y / factor_y if abs(factor_y) > tolerance else frame.scale_y

        self._sync_layer_source_frames(layer)
        return True

    def _clear_user_offsets_for_layers(self, layer_ids: Set[int]) -> None:
        """Remove gizmo offsets after they have been baked into keyframes."""
        for layer_id in layer_ids:
            self.gl_widget.layer_offsets.pop(layer_id, None)
            self.gl_widget.layer_rotations.pop(layer_id, None)
            self.gl_widget.layer_scale_offsets.pop(layer_id, None)
            self.gl_widget.layer_anchor_overrides.pop(layer_id, None)

    def _capture_keyframe_state(self, layer_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """Return deep copies of keyframes and anchor data for the provided layers."""
        snapshot: Dict[int, Dict[str, Any]] = {}
        for layer_id in layer_ids:
            layer = self.gl_widget.get_layer_by_id(layer_id)
            if not layer:
                continue
            snapshot[layer_id] = {
                "keyframes": [replace(kf) for kf in layer.keyframes],
                "anchor": (float(layer.anchor_x), float(layer.anchor_y)),
            }
        return snapshot

    def _begin_keyframe_action(self, layer_ids: List[int]):
        unique = sorted({layer_id for layer_id in layer_ids if layer_id is not None})
        if not unique:
            self._pending_keyframe_action = None
            return
        self._pending_keyframe_action = {
            'layer_ids': unique,
            'before': self._capture_keyframe_state(unique),
        }

    def _finalize_keyframe_action(self, label: str):
        if not self._pending_keyframe_action:
            return
        layer_ids = self._pending_keyframe_action['layer_ids']
        before_state = self._pending_keyframe_action['before']
        after_state = self._capture_keyframe_state(layer_ids)
        self._pending_keyframe_action = None
        if before_state == after_state:
            return
        action = {
            'label': label,
            'layer_ids': layer_ids,
            'before': before_state,
            'after': after_state,
            'type': 'keyframe',
        }
        self._push_history_action(action)

    def _apply_keyframe_snapshot(self, snapshot: Dict[int, Dict[str, Any]]) -> None:
        """Replace layer keyframes/anchors with the provided snapshot."""
        changed = False
        for layer_id, payload in snapshot.items():
            layer = self.gl_widget.get_layer_by_id(layer_id)
            if not layer:
                continue
            frames: List[KeyframeData]
            anchor_value = None
            if isinstance(payload, dict):
                frames = payload.get("keyframes", [])
                anchor_value = payload.get("anchor")
            else:
                frames = payload  # Backwards compatibility with older snapshots
            layer.keyframes = [replace(kf) for kf in frames]
            layer.keyframes.sort(key=lambda frame: frame.time)
            self._sync_layer_source_frames(layer)
            if anchor_value is not None:
                self._update_layer_anchor(layer, anchor_value)
            changed = True
        if changed:
            self.gl_widget.player.calculate_duration()
            self.update_timeline()
            self._refresh_timeline_keyframes()
            self.gl_widget.update()

    def _update_layer_anchor(
        self,
        layer: LayerData,
        anchor: Optional[Tuple[float, float]],
        tolerance: float = 1e-4,
    ) -> bool:
        """Apply a new anchor value to the layer and mirror it to JSON/cache."""
        if not layer or anchor is None:
            return False
        target_x = float(anchor[0])
        target_y = float(anchor[1])
        if (
            abs(layer.anchor_x - target_x) < tolerance
            and abs(layer.anchor_y - target_y) < tolerance
        ):
            return False
        layer.anchor_x = target_x
        layer.anchor_y = target_y
        source = self.layer_source_lookup.get(layer.layer_id)
        if source is not None:
            source["anchor_x"] = target_x
            source["anchor_y"] = target_y
            anchor_block = source.get("anchor")
            if isinstance(anchor_block, dict):
                anchor_block["x"] = target_x
                anchor_block["y"] = target_y
        if self.base_layer_cache:
            for cached in self.base_layer_cache:
                if cached.layer_id == layer.layer_id:
                    cached.anchor_x = target_x
                    cached.anchor_y = target_y
                    break
        return True

    def _reset_edit_history(self):
        """Clear undo/redo stacks for all edits."""
        self._history_stack.clear()
        self._history_redo_stack.clear()
        self._pending_keyframe_action = None
        self._update_keyframe_history_controls()

    def _update_keyframe_history_controls(self):
        """Update control panel buttons based on undo stack state."""
        undo_available = bool(self._history_stack) and self._history_stack[-1].get('type') == 'keyframe'
        redo_available = bool(self._history_redo_stack) and self._history_redo_stack[-1].get('type') == 'keyframe'
        self.control_panel.set_keyframe_history_state(undo_available, redo_available)

    def undo_keyframe_action(self) -> bool:
        """Undo the most recent keyframe edit."""
        if self._undo_history_action(required_type='keyframe'):
            return True
        self.log_widget.log("No keyframe edits to undo.", "INFO")
        return False

    def redo_keyframe_action(self) -> bool:
        """Redo the last undone keyframe edit."""
        if self._redo_history_action(required_type='keyframe'):
            return True
        self.log_widget.log("No keyframe edits to redo.", "INFO")
        return False

    def delete_other_keyframes(self):
        """Flatten the animation so only the current pose remains for every layer."""
        animation = self.gl_widget.player.animation
        if not animation:
            self.log_widget.log("Load an animation before modifying keyframes.", "WARNING")
            return
        target_layer_ids: List[int]
        if self.selected_layer_ids:
            target_layer_ids = [
                layer.layer_id
                for layer in animation.layers
                if layer.layer_id in self.selected_layer_ids
            ]
            if not target_layer_ids:
                self.log_widget.log(
                    "Selected layers are not available in this animation.",
                    "WARNING"
                )
                return
        else:
            target_layer_ids = [
                layer.layer_id for layer in animation.layers if layer.layer_id is not None
            ]
            if not target_layer_ids:
                self.log_widget.log("This animation has no editable layers.", "WARNING")
                return
        layer_ids = target_layer_ids
        target_layer_set = set(layer_ids)

        # Build lookup tables for hierarchy traversal
        layer_lookup: Dict[int, LayerData] = {}
        for layer in animation.layers:
            if layer.layer_id is not None:
                layer_lookup[layer.layer_id] = layer

        children_map: Dict[int, List[int]] = {}
        root_layer_ids: List[int] = []
        for layer in animation.layers:
            if layer.layer_id is None:
                continue
            if layer.layer_id not in target_layer_set:
                continue
            parent_id = layer.parent_id
            if parent_id is None or parent_id < 0 or parent_id not in layer_lookup:
                root_layer_ids.append(layer.layer_id)
            else:
                children_map.setdefault(parent_id, []).append(layer.layer_id)

        current_time = round(self.gl_widget.player.current_time, 5)
        original_time = self.gl_widget.player.current_time
        self.gl_widget.player.current_time = current_time
        self._begin_keyframe_action(layer_ids)

        # Only layers with pending gizmo offsets/overrides need pose baking
        pose_layer_ids = {
            layer_id for layer_id in layer_ids
            if layer_id is not None and layer_id in layer_lookup
        }
        pose_bake_required = bool(pose_layer_ids)
        evaluated_world_state = self.gl_widget._build_layer_world_states(current_time)
        pose_state_cache: Dict[
            Tuple[int, ...],
            Tuple[Dict[int, Dict[str, Any]], Dict[int, Dict[str, Any]]]
        ] = {}

        def pose_state_key(layer_id: int) -> Tuple[int, ...]:
            chain: List[int] = []
            current = layer_lookup.get(layer_id)
            visited: Set[int] = set()
            while current:
                lid = current.layer_id
                if lid in visited or lid is None:
                    break
                visited.add(lid)
                chain.append(lid)
                parent_id = current.parent_id
                current = layer_lookup.get(parent_id)
            chain.sort()
            return tuple(chain) if chain else (layer_id,)

        captured = 0
        for layer in animation.layers:
            if layer.layer_id is None:
                continue
            if layer.layer_id not in target_layer_set:
                continue
            if pose_bake_required and layer.layer_id in pose_layer_ids:
                cache_key = pose_state_key(layer.layer_id)
                base_states_map, final_states_map = pose_state_cache.get(cache_key, ({}, {}))
                if not base_states_map or not final_states_map:
                    ids = set(cache_key)
                    base_states_map, final_states_map = self._gather_pose_state_maps(ids)
                    pose_state_cache[cache_key] = (base_states_map, final_states_map)
                if self._record_pose_for_layer(
                    layer.layer_id,
                    current_time,
                    "current",
                    base_states_map,
                    final_states_map,
                    force=True,
                ):
                    captured += 1
            render_snapshot = evaluated_world_state.get(layer.layer_id) if evaluated_world_state else None
            layer_state = self.gl_widget.player.get_layer_state(layer, current_time)
            keyframe = self._find_keyframe_at_time(layer, current_time, tolerance=1e-4)
            snapshot_sprite = None
            if render_snapshot:
                snapshot_sprite = render_snapshot.get('sprite_name')
            if not snapshot_sprite:
                snapshot_sprite = layer_state.get('sprite_name')
            if keyframe:
                if not snapshot_sprite:
                    snapshot_sprite = keyframe.sprite_name
                if snapshot_sprite:
                    keyframe.sprite_name = snapshot_sprite
                    if keyframe.immediate_sprite == -1:
                        keyframe.immediate_sprite = 1
                layer.keyframes = [replace(keyframe)]
            else:
                # Capture the interpolated state at current time before clearing keyframes
                layer.keyframes = [KeyframeData(
                    time=current_time,
                    pos_x=float(layer_state.get('pos_x', 0.0)),
                    pos_y=float(layer_state.get('pos_y', 0.0)),
                    scale_x=float(layer_state.get('scale_x', 100.0)),
                    scale_y=float(layer_state.get('scale_y', 100.0)),
                    rotation=float(layer_state.get('rotation', 0.0)),
                    opacity=float(layer_state.get('opacity', 100.0)),
                    sprite_name=layer_state.get('sprite_name', ''),
                    r=int(layer_state.get('r', 255)),
                    g=int(layer_state.get('g', 255)),
                    b=int(layer_state.get('b', 255)),
                    immediate_pos=1,  # NONE interpolation - hold values
                    immediate_scale=1,
                    immediate_rotation=1,
                    immediate_opacity=1,
                    immediate_sprite=1,
                    immediate_rgb=1
                )]
            layer.keyframes.sort(key=lambda frame: frame.time)
            self._sync_layer_source_frames(layer)
        self._clear_user_offsets_for_layers(set(layer_ids))
        self._finalize_keyframe_action("delete_other_keyframes")
        self.gl_widget.player.current_time = original_time
        self.gl_widget.player.calculate_duration()
        self.update_timeline()
        self._refresh_timeline_keyframes()
        self.gl_widget.update()
        self.log_widget.log(
            "Captured the current pose for every layer and removed all other keyframes.",
            "SUCCESS"
        )

    def extend_animation_duration_dialog(self):
        """Prompt the user for a new animation duration."""
        animation = self.gl_widget.player.animation
        if not animation:
            self.log_widget.log("Load an animation before extending it.", "WARNING")
            return
        current_duration = float(max(self.gl_widget.player.duration, 0.0))
        suggested = max(current_duration, 0.1)
        minimum = 1e-3
        new_duration, ok = QInputDialog.getDouble(
            self,
            "Set Animation Duration",
            "New total duration (seconds):",
            suggested,
            minimum,
            3600.0,
            3
        )
        if not ok:
            return
        self._set_animation_duration(float(new_duration))

    def _set_animation_duration(self, new_duration: float):
        """Adjust animation length, extending or trimming keyframes."""
        animation = self.gl_widget.player.animation
        if not animation:
            self.log_widget.log("Load an animation before extending it.", "WARNING")
            return
        current_duration = float(max(self.gl_widget.player.duration, 0.0))
        if new_duration <= 0.0:
            self.log_widget.log("Duration must be greater than zero seconds.", "WARNING")
            return
        if abs(new_duration - current_duration) <= 1e-6:
            self.log_widget.log("Duration unchanged.", "INFO")
            return
        layer_ids = [layer.layer_id for layer in animation.layers if layer.layer_id is not None]
        if not layer_ids:
            self.log_widget.log("This animation has no editable layers.", "WARNING")
            return
        self._begin_keyframe_action(layer_ids)
        trimmed_snapshots: Dict[int, Dict[str, Any]] = {}
        shortening = new_duration < current_duration
        tolerance = 1e-4
        if shortening:
            for layer in animation.layers:
                if layer.layer_id is None:
                    continue
                trimmed_snapshots[layer.layer_id] = self.gl_widget.player.get_layer_state(layer, new_duration)
        for layer in animation.layers:
            if layer.layer_id is None:
                continue
            if not shortening:
                if layer.keyframes:
                    last_keyframe = max(layer.keyframes, key=lambda frame: frame.time)
                    duplicated = replace(last_keyframe)
                else:
                    duplicated = KeyframeData(time=new_duration)
                duplicated.time = new_duration
                layer.keyframes.append(duplicated)
            else:
                snapshots = trimmed_snapshots.get(layer.layer_id, {})
                preserved: List[KeyframeData] = []
                for keyframe in layer.keyframes:
                    if keyframe.time < new_duration - tolerance:
                        preserved.append(replace(keyframe))
                    elif abs(keyframe.time - new_duration) <= tolerance:
                        clone = replace(keyframe)
                        clone.time = new_duration
                        preserved.append(clone)
                    # Keyframes after the new duration are discarded
                if not preserved or preserved[-1].time < new_duration - tolerance:
                    preserved.append(
                        KeyframeData(
                            time=new_duration,
                            pos_x=float(snapshots.get('pos_x', 0.0)),
                            pos_y=float(snapshots.get('pos_y', 0.0)),
                            scale_x=float(snapshots.get('scale_x', 100.0)),
                            scale_y=float(snapshots.get('scale_y', 100.0)),
                            rotation=float(snapshots.get('rotation', 0.0)),
                            opacity=float(snapshots.get('opacity', 100.0)),
                            sprite_name=snapshots.get('sprite_name', ''),
                            r=int(snapshots.get('r', 255)),
                            g=int(snapshots.get('g', 255)),
                            b=int(snapshots.get('b', 255)),
                            immediate_pos=1,
                            immediate_scale=1,
                            immediate_rotation=1,
                            immediate_opacity=1,
                            immediate_sprite=1,
                            immediate_rgb=1
                        )
                    )
                layer.keyframes = preserved
            layer.keyframes.sort(key=lambda frame: frame.time)
            self._sync_layer_source_frames(layer)
        action_label = "shrink_duration" if shortening else "extend_duration"
        self._finalize_keyframe_action(action_label)
        self.gl_widget.player.calculate_duration()
        if self.gl_widget.player.current_time > new_duration:
            self.gl_widget.player.current_time = new_duration
        self.update_timeline()
        self._refresh_timeline_keyframes()
        self.gl_widget.update()
        verb = "Shortened" if shortening else "Extended"
        self.log_widget.log(f"{verb} animation to {new_duration:.3f} seconds.", "SUCCESS")

    def _gather_pose_state_maps(
        self,
        target_ids: Set[int]
    ) -> Tuple[Dict[int, Dict[str, Any]], Dict[int, Dict[str, Any]]]:
        """Return (base_states_without_offsets, final_states_with_offsets)."""
        if not target_ids:
            return {}, {}
        final_raw = self.gl_widget._build_layer_world_states()
        final_map = copy.deepcopy(final_raw)
        snapshots: Dict[int, Dict[str, Any]] = {}
        for layer_id in target_ids:
            snapshots[layer_id] = {
                "offset": self.gl_widget.layer_offsets.get(layer_id),
                "rotation": self.gl_widget.layer_rotations.get(layer_id),
                "scale": self.gl_widget.layer_scale_offsets.get(layer_id),
                "anchor": self.gl_widget.layer_anchor_overrides.get(layer_id),
            }
            self.gl_widget.layer_offsets[layer_id] = (0.0, 0.0)
            self.gl_widget.layer_rotations[layer_id] = 0.0
            self.gl_widget.layer_scale_offsets[layer_id] = (1.0, 1.0)
            if layer_id in self.gl_widget.layer_anchor_overrides:
                self.gl_widget.layer_anchor_overrides.pop(layer_id, None)
        base_raw = copy.deepcopy(self.gl_widget._build_layer_world_states())
        for layer_id, snapshot in snapshots.items():
            offset_val = snapshot.get("offset")
            rot_val = snapshot.get("rotation")
            scale_val = snapshot.get("scale")
            anchor_val = snapshot.get("anchor")
            if offset_val is None:
                self.gl_widget.layer_offsets.pop(layer_id, None)
            else:
                self.gl_widget.layer_offsets[layer_id] = offset_val
            if rot_val is None:
                self.gl_widget.layer_rotations.pop(layer_id, None)
            else:
                self.gl_widget.layer_rotations[layer_id] = rot_val
            if scale_val is None:
                self.gl_widget.layer_scale_offsets.pop(layer_id, None)
            else:
                self.gl_widget.layer_scale_offsets[layer_id] = scale_val
            if anchor_val is None:
                self.gl_widget.layer_anchor_overrides.pop(layer_id, None)
            else:
                self.gl_widget.layer_anchor_overrides[layer_id] = anchor_val
        self.gl_widget._build_layer_world_states()
        return base_raw, final_map

    def _world_delta_to_local(
        self,
        layer: LayerData,
        state_map: Dict[int, Dict[str, Any]],
        world_dx: float,
        world_dy: float,
    ) -> Tuple[float, float]:
        """Convert a world-space offset into the parent's local coordinates."""
        parent_id = layer.parent_id
        if parent_id is None or parent_id < 0:
            return world_dx, world_dy
        parent_state = state_map.get(parent_id)
        if not parent_state:
            return world_dx, world_dy
        pm00 = float(parent_state.get("m00", 1.0))
        pm01 = float(parent_state.get("m01", 0.0))
        pm10 = float(parent_state.get("m10", 0.0))
        pm11 = float(parent_state.get("m11", 1.0))
        det = pm00 * pm11 - pm01 * pm10
        if abs(det) < 1e-6:
            return world_dx, world_dy
        inv00 = pm11 / det
        inv01 = -pm01 / det
        inv10 = -pm10 / det
        inv11 = pm00 / det
        local_x = inv00 * world_dx + inv01 * world_dy
        local_y = inv10 * world_dx + inv11 * world_dy
        return local_x, local_y

    def _find_keyframe_at_time(
        self,
        layer: LayerData,
        time_value: float,
        tolerance: float = 1.0 / 600.0
    ) -> Optional[KeyframeData]:
        """Return the first keyframe whose timestamp is within tolerance."""
        for keyframe in layer.keyframes:
            if abs(keyframe.time - time_value) <= tolerance:
                return keyframe
        return None

    def _sync_layer_source_frames(self, layer: LayerData) -> None:
        """Mirror dataclass keyframes back to the source JSON structure."""
        source = self.layer_source_lookup.get(layer.layer_id)
        if source is None:
            return
        serialized = [self._serialize_keyframe(keyframe) for keyframe in layer.keyframes]
        source["frames"] = serialized
        if self.base_layer_cache:
            for cached in self.base_layer_cache:
                if cached.layer_id == layer.layer_id:
                    cached.keyframes = [replace(kf) for kf in layer.keyframes]
                    break

    def _serialize_keyframe(self, keyframe: KeyframeData) -> Dict[str, Any]:
        """Convert a KeyframeData instance back into the JSON frame schema."""
        def _int(value: Any, default: int = 0) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        return {
            "time": float(keyframe.time),
            "pos": {
                "x": float(keyframe.pos_x),
                "y": float(keyframe.pos_y),
                "immediate": _int(keyframe.immediate_pos),
            },
            "scale": {
                "x": float(keyframe.scale_x),
                "y": float(keyframe.scale_y),
                "immediate": _int(keyframe.immediate_scale),
            },
            "rotation": {
                "value": float(keyframe.rotation),
                "immediate": _int(keyframe.immediate_rotation),
            },
            "opacity": {
                "value": float(keyframe.opacity),
                "immediate": _int(keyframe.immediate_opacity),
            },
            "sprite": {
                "string": keyframe.sprite_name or "",
                "immediate": _int(keyframe.immediate_sprite),
            },
            "rgb": {
                "red": int(keyframe.r),
                "green": int(keyframe.g),
                "blue": int(keyframe.b),
                "immediate": _int(keyframe.immediate_rgb, -1),
            },
        }

    def _export_animation_dict(self, animation: AnimationData) -> Dict[str, Any]:
        """Serialize the active AnimationData and edited layer metadata."""
        exported = {
            "name": animation.name,
            "width": int(animation.width),
            "height": int(animation.height),
            "loop_offset": float(animation.loop_offset),
            "centered": int(animation.centered),
            "layers": []
        }
        skipped_layers: List[str] = []
        for layer in animation.layers:
            source = self.layer_source_lookup.get(layer.layer_id)
            if source is None:
                label = layer.name or f"Layer {layer.layer_id}"
                skipped_layers.append(label)
                continue
            layer_dict = copy.deepcopy(source) if source else {}
            layer_dict["name"] = layer.name
            layer_dict["id"] = layer.layer_id
            layer_dict["parent"] = layer.parent_id
            layer_dict["anchor_x"] = float(layer.anchor_x)
            layer_dict["anchor_y"] = float(layer.anchor_y)
            layer_dict["blend"] = int(layer.blend_mode)
            layer_dict["visible"] = bool(layer.visible)
            if layer.shader_name:
                layer_dict["shader"] = layer.shader_name
            else:
                layer_dict.pop("shader", None)

            def _assign_color(field_name: str, value: Optional[Tuple[float, float, float, float]]):
                if value is None:
                    layer_dict.pop(field_name, None)
                    return
                layer_dict[field_name] = [float(component) for component in value]

            _assign_color("color_tint", getattr(layer, "color_tint", None))
            _assign_color("color_tint_hdr", getattr(layer, "color_tint_hdr", None))

            gradient = getattr(layer, "color_gradient", None)
            if gradient:
                layer_dict["color_gradient"] = copy.deepcopy(gradient)
            else:
                layer_dict.pop("color_gradient", None)

            animator = getattr(layer, "color_animator", None)
            if animator:
                layer_dict["color_animator"] = copy.deepcopy(animator)
            else:
                layer_dict.pop("color_animator", None)

            metadata = getattr(layer, "color_metadata", None)
            if metadata:
                layer_dict["color_metadata"] = copy.deepcopy(metadata)
            else:
                layer_dict.pop("color_metadata", None)

            render_tags = getattr(layer, "render_tags", set())
            if render_tags:
                layer_dict["render_tags"] = sorted(tag for tag in render_tags if isinstance(tag, str))
            else:
                layer_dict.pop("render_tags", None)

            mask_role = getattr(layer, "mask_role", None)
            mask_key = getattr(layer, "mask_key", None)
            if mask_role:
                layer_dict["mask_role"] = mask_role
            else:
                layer_dict.pop("mask_role", None)
            if mask_key:
                layer_dict["mask_key"] = mask_key
            else:
                layer_dict.pop("mask_key", None)

            layer_dict["frames"] = [self._serialize_keyframe(keyframe) for keyframe in layer.keyframes]
            exported["layers"].append(layer_dict)
        if skipped_layers and self.log_widget:
            preview = ", ".join(skipped_layers[:3])
            if len(skipped_layers) > 3:
                preview += ", ..."
            self.log_widget.log(
                f"Skipped {len(skipped_layers)} transient layer(s) without JSON metadata during export: {preview}",
                "DEBUG",
            )
        return exported

    def _ensure_payload_defaults(self, payload: Dict[str, Any]) -> None:
        """Guarantee payload has required top-level fields."""
        blend = payload.get("blend_version")
        if not isinstance(blend, int) or blend <= 0:
            payload["blend_version"] = self.current_blend_version or 1
        rev_value = payload.get("rev")
        if not isinstance(rev_value, int) or rev_value <= 0:
            payload["rev"] = 6
        if not isinstance(payload.get("sources"), list):
            payload["sources"] = []
        if not isinstance(payload.get("anims"), list):
            payload["anims"] = []

    def _inject_animation_into_payload(self, payload: Dict[str, Any], animation: AnimationData) -> None:
        """Merge exported animation data into an existing payload."""
        self._ensure_payload_defaults(payload)
        exported = self._export_animation_dict(animation)
        anims = payload.get("anims") or []
        idx = self.current_animation_index
        if 0 <= idx < len(anims) and isinstance(anims[idx], dict):
            anims[idx].update(exported)
            payload["anims"] = anims
            return
        if animation.name:
            target_name = animation.name.lower()
            for entry in anims:
                if isinstance(entry, dict) and (entry.get("name") or "").lower() == target_name:
                    entry.update(exported)
                    payload["anims"] = anims
                    return
        anims.append(exported)
        payload["anims"] = anims
    
    def on_scale_changed(self, value: float):
        """Handle render scale change"""
        self.gl_widget.render_scale = value
        self.gl_widget.update()
    
    def on_fps_changed(self, value: int):
        """Handle FPS change"""
        interval = int(1000 / value)
        self.gl_widget.timer.setInterval(interval)
    
    def on_position_scale_changed(self, value: float):
        """Handle position scale spinbox change"""
        self.gl_widget.position_scale = value
        # Update slider without triggering its signal
        self.control_panel.pos_scale_slider.blockSignals(True)
        self.control_panel.pos_scale_slider.setValue(int(value * 100))
        self.control_panel.pos_scale_slider.blockSignals(False)
        self.gl_widget.update()
    
    def on_position_scale_slider_changed(self, value: int):
        """Handle position scale slider change"""
        scale_value = value / 100.0
        # Update spinbox without triggering its signal
        self.control_panel.pos_scale_spin.blockSignals(True)
        self.control_panel.pos_scale_spin.setValue(scale_value)
        self.control_panel.pos_scale_spin.blockSignals(False)
        self.gl_widget.position_scale = scale_value
        self.gl_widget.update()
    
    def on_base_world_scale_changed(self, value: float):
        """Handle base world scale spinbox change"""
        self.gl_widget.renderer.base_world_scale = value
        # Update slider without triggering its signal
        self.control_panel.base_scale_slider.blockSignals(True)
        self.control_panel.base_scale_slider.setValue(int(value * 100))
        self.control_panel.base_scale_slider.blockSignals(False)
        self.gl_widget.update()
    
    def on_base_world_scale_slider_changed(self, value: int):
        """Handle base world scale slider change"""
        scale_value = value / 100.0
        # Update spinbox without triggering its signal
        self.control_panel.base_scale_spin.blockSignals(True)
        self.control_panel.base_scale_spin.setValue(scale_value)
        self.control_panel.base_scale_spin.blockSignals(False)
        self.gl_widget.renderer.base_world_scale = scale_value
        self.gl_widget.update()
    
    def on_translation_sensitivity_changed(self, value: float):
        """Adjust sprite drag translation speed multiplier."""
        self.gl_widget.drag_translation_multiplier = max(0.01, value)
    
    def on_rotation_sensitivity_changed(self, value: float):
        """Adjust sprite rotation sensitivity multiplier."""
        self.gl_widget.drag_rotation_multiplier = max(0.1, value)
    
    def on_rotation_overlay_size_changed(self, value: float):
        """Adjust the visual radius of the rotation gizmo."""
        self.gl_widget.rotation_overlay_radius = max(5.0, value)
        self.gl_widget.update()
    
    def toggle_rotation_gizmo(self, enabled: bool):
        """Toggle visibility of the rotation gizmo overlay."""
        self.gl_widget.rotation_gizmo_enabled = enabled
        if enabled and not self.selected_layer_ids and self.gl_widget.player.animation:
            first_layer = self.gl_widget.player.animation.layers[0]
            self.selected_layer_ids = {first_layer.layer_id}
            self.primary_selected_layer_id = first_layer.layer_id
            self.selection_lock_enabled = False
            self.layer_panel.set_selection_state(self.selected_layer_ids)
            self.apply_selection_state()
        self.gl_widget.update()

    def on_audio_enabled_changed(self, enabled: bool):
        """Enable or mute audio playback."""
        self.audio_manager.set_enabled(enabled)
        if enabled and self.gl_widget.player.playing and self.audio_manager.is_ready:
            self.audio_manager.play(self.gl_widget.player.current_time)
        elif not enabled:
            self.audio_manager.pause()
        state = "enabled" if enabled else "muted"
        self.log_widget.log(f"Audio {state}", "INFO")

    def on_audio_volume_changed(self, value: int):
        """Adjust playback volume."""
        self.audio_manager.set_volume(value)

    def toggle_antialiasing(self, enabled: bool):
        """Enable or disable multi-sample anti-aliasing in the OpenGL view."""
        self.gl_widget.set_antialiasing_enabled(enabled)
        self.gl_widget.update()

    def on_tweening_toggled(self, enabled: bool):
        """Enable or disable tweening (linear interpolation) across players."""
        try:
            self.gl_widget.set_tweening_enabled(enabled)
        except Exception:
            pass
        if getattr(self, '_pose_baseline_player', None):
            try:
                self._pose_baseline_player.tweening_enabled = enabled
            except Exception:
                pass

    def on_glitch_jitter_toggled(self, enabled: bool):
        """Enable/disable jitter glitching for animation players."""
        try:
            self.gl_widget.set_glitch_jitter_enabled(enabled)
        except Exception:
            pass
        if getattr(self, '_pose_baseline_player', None):
            try:
                self._pose_baseline_player.glitch_jitter_enabled = enabled
                # keep amplitude in sync if present
                self._pose_baseline_player.jitter_amplitude = getattr(self.gl_widget, 'jitter_amplitude', 1.0)
            except Exception:
                pass

    def on_glitch_jitter_amount_changed(self, amount: float):
        try:
            self.gl_widget.set_glitch_jitter_amount(amount)
        except Exception:
            pass
        if getattr(self, '_pose_baseline_player', None):
            try:
                self._pose_baseline_player.jitter_amplitude = float(amount)
            except Exception:
                pass

    def on_glitch_sprite_toggled(self, enabled: bool):
        """Enable/disable sprite flicker glitch."""
        try:
            self.gl_widget.set_glitch_sprite_enabled(enabled)
        except Exception:
            pass
        if getattr(self, '_pose_baseline_player', None):
            try:
                self._pose_baseline_player.glitch_sprite_enabled = enabled
                self._pose_baseline_player.glitch_sprite_chance = getattr(self.gl_widget, 'glitch_sprite_chance', 0.1)
            except Exception:
                pass

    def on_glitch_sprite_chance_changed(self, chance: float):
        try:
            self.gl_widget.set_glitch_sprite_chance(chance)
        except Exception:
            pass
        if getattr(self, '_pose_baseline_player', None):
            try:
                self._pose_baseline_player.glitch_sprite_chance = float(chance)
            except Exception:
                pass

    def toggle_scale_gizmo(self, enabled: bool):
        """Toggle the scaling gizmo overlay."""
        self.gl_widget.set_scale_gizmo_enabled(enabled)
        self.gl_widget.update()

    def on_scale_mode_changed(self, mode: str):
        """Change scale gizmo mode (uniform/per-axis)."""
        self.gl_widget.set_scale_gizmo_mode(mode)

    def _sync_audio_playback(self, playing: bool):
        """Start or pause the audio player to match animation playback."""
        if not self.audio_manager.is_ready:
            return
        if playing:
            self.audio_manager.play(self.gl_widget.player.current_time)
        else:
            self.audio_manager.pause()
    
    def on_anchor_bias_x_changed(self, value: float):
        self.gl_widget.renderer.anchor_bias_x = value
        self.gl_widget.update()
    
    def on_anchor_bias_y_changed(self, value: float):
        self.gl_widget.renderer.anchor_bias_y = value
        self.gl_widget.update()
    
    def on_local_position_multiplier_changed(self, value: float):
        self.gl_widget.renderer.local_position_multiplier = max(0.0, value)
        self.gl_widget.update()
    
    def on_parent_mix_changed(self, value: float):
        self.gl_widget.renderer.parent_mix = max(0.0, min(1.0, value))
        self.gl_widget.update()
    
    def on_rotation_bias_changed(self, value: float):
        self.gl_widget.renderer.rotation_bias = value
        self.gl_widget.update()
    
    def on_scale_bias_x_changed(self, value: float):
        self.gl_widget.renderer.scale_bias_x = max(0.0, value)
        self.gl_widget.update()
    
    def on_scale_bias_y_changed(self, value: float):
        self.gl_widget.renderer.scale_bias_y = max(0.0, value)
        self.gl_widget.update()
    
    def on_world_offset_x_changed(self, value: float):
        self.gl_widget.renderer.world_offset_x = value
        self.gl_widget.update()
    
    def on_world_offset_y_changed(self, value: float):
        self.gl_widget.renderer.world_offset_y = value
        self.gl_widget.update()
    
    def on_trim_shift_multiplier_changed(self, value: float):
        self.gl_widget.renderer.trim_shift_multiplier = max(0.0, value)
        self.gl_widget.update()
    
    def reset_camera(self):
        """Reset camera to default position"""
        self.gl_widget.reset_camera()
        self.control_panel.scale_spin.setValue(1.0)
    
    def fit_to_view(self):
        """Fit the animation to the viewport"""
        if self.gl_widget.fit_to_view():
            # Update the scale spinbox to reflect the new scale
            self.control_panel.scale_spin.blockSignals(True)
            self.control_panel.scale_spin.setValue(self.gl_widget.render_scale)
            self.control_panel.scale_spin.blockSignals(False)
            self.log_widget.log("Fitted animation to view", "SUCCESS")
        else:
            self.log_widget.log("No animation to fit", "WARNING")
    
    def toggle_bone_overlay(self, enabled: bool):
        """Toggle the bone/skeleton overlay"""
        self.gl_widget.show_bones = enabled
        self.gl_widget.update()
    
    def toggle_anchor_overlay(self, enabled: bool):
        """Toggle the anchor overlay/editor."""
        self.gl_widget.set_anchor_overlay_enabled(enabled)

    def toggle_parent_overlay(self, enabled: bool):
        """Toggle the parent overlay/editor."""
        self.gl_widget.set_parent_overlay_enabled(enabled)

    def on_anchor_drag_precision_changed(self, value: float):
        """Update how sensitive anchor dragging is."""
        self.gl_widget.set_anchor_drag_precision(value)

    def on_bpm_value_changed(self, value: float):
        """Handle BPM slider/spin edits."""
        self._set_current_bpm(value, update_ui=False, store_override=True)
        self._update_audio_speed()

    def on_sync_audio_to_bpm_toggled(self, enabled: bool):
        """Toggle whether audio speed follows BPM."""
        self.sync_audio_to_bpm = enabled
        self.settings.setValue('audio/sync_to_bpm', enabled)
        self._update_audio_speed()

    def on_pitch_shift_toggled(self, enabled: bool):
        """Toggle pitch shifting for audio playback."""
        self.pitch_shift_enabled = enabled
        self.settings.setValue('audio/pitch_shift_enabled', enabled)
        self._update_audio_speed()

    def on_solid_bg_enabled_changed(self, enabled: bool):
        """Handle background fill checkbox toggles."""
        self.solid_bg_enabled = bool(enabled)
        self.settings.setValue('export/solid_bg_enabled', self.solid_bg_enabled)

    def on_solid_bg_color_changed(self, r: int, g: int, b: int, a: int):
        """Update the stored export background color."""
        self._apply_solid_bg_color((r, g, b, a), announce=False)

    def on_remove_transparency_toggled(self, enabled: bool):
        """Toggle forcing opaque rendering in viewer."""
        self.force_opaque = bool(enabled)
        self.settings.setValue('viewer/force_opaque', self.force_opaque)
        if hasattr(self, 'gl_widget') and self.gl_widget:
            self.gl_widget.set_force_opaque(self.force_opaque)

    def on_auto_background_color_requested(self):
        """Attempt to find a color not present in current sprite textures."""
        suggestion = self._suggest_unused_background_color()
        if suggestion:
            self._apply_solid_bg_color(suggestion, announce=True)
            self.control_panel.set_solid_bg_color(suggestion)
            self.log_widget.log(
                "Unique background color suggestion is based on the current sprite atlas pixels.",
                "INFO",
            )
        else:
            self.log_widget.log(
                "Unable to find a unique background color; try selecting one manually.",
                "WARNING",
            )

    def on_reset_bpm_requested(self):
        """Reset BPM to detected base for current animation."""
        if self.current_animation_name and self.current_animation_name in self.animation_bpm_overrides:
            del self.animation_bpm_overrides[self.current_animation_name]
        token = self._current_monster_token()
        token_key = token.lower() if token else None
        if token_key and token_key in self.monster_base_bpm_overrides:
            del self.monster_base_bpm_overrides[token_key]
            self._save_base_bpm_overrides()
            if token:
                self.log_widget.log(f"Cleared locked BPM for {token}.", "INFO")
        self._configure_animation_bpm()

    def on_lock_base_bpm_requested(self):
        """Prompt the user to lock the base BPM for the current monster."""
        token = self._current_monster_token()
        if not token:
            self.log_widget.log("Load a monster before locking its BPM.", "WARNING")
            return
        initial = max(20.0, min(300.0, float(self.current_base_bpm or 120.0)))
        value, ok = QInputDialog.getDouble(
            self,
            "Lock Base BPM",
            f"Set the base BPM for {token}:",
            initial,
            20.0,
            300.0,
            1,
        )
        if not ok:
            return
        locked_value = max(20.0, min(300.0, float(value)))
        previous_base = max(1e-3, self.current_base_bpm)
        playback_ratio = self.current_bpm / previous_base if previous_base > 0 else 1.0
        self.current_base_bpm = locked_value
        new_bpm = max(20.0, min(300.0, playback_ratio * locked_value))
        token_key = token.lower()
        self.monster_base_bpm_overrides[token_key] = locked_value
        self._save_base_bpm_overrides()
        self._set_current_bpm(new_bpm, update_ui=True, store_override=False)
        self.log_widget.log(
            f"Locked base BPM for {token} at {locked_value:.1f}.",
            "SUCCESS",
        )

    def render_frame_to_image(
        self,
        width: int,
        height: int,
        *,
        camera_override: Optional[Tuple[float, float]] = None,
        render_scale_override: Optional[float] = None,
        apply_centering: bool = True,
        background_color: Optional[Tuple[int, int, int, int]] = None,
    ) -> Optional[Image.Image]:
        """
        Render the current frame to a PIL Image.
        """
        fbo = None
        texture = None
        default_fbo = None
        viewport_before = (0, 0, self.gl_widget.width(), self.gl_widget.height())
        projection_pushed = False
        modelview_pushed = False
        try:
            self.gl_widget.makeCurrent()
            default_fbo = self.gl_widget.defaultFramebufferObject()
            viewport_before = glGetIntegerv(GL_VIEWPORT)
            fbo = glGenFramebuffers(1)
            glBindFramebuffer(GL_FRAMEBUFFER, fbo)
            texture = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, texture)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, None)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, texture, 0)
            if glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE:
                self.log_widget.log("Framebuffer not complete", "ERROR")
                return None
            glViewport(0, 0, width, height)
            glMatrixMode(GL_PROJECTION)
            glPushMatrix()
            projection_pushed = True
            glLoadIdentity()
            glOrtho(0, width, height, 0, -1, 1)
            glMatrixMode(GL_MODELVIEW)
            glPushMatrix()
            modelview_pushed = True
            glClearColor(0.0, 0.0, 0.0, 0.0)
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            glEnable(GL_BLEND)
            glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
            glEnable(GL_TEXTURE_2D)
            camera_x = camera_override[0] if camera_override else self.gl_widget.camera_x
            camera_y = camera_override[1] if camera_override else self.gl_widget.camera_y
            render_scale = render_scale_override if render_scale_override is not None else self.gl_widget.render_scale
            if self.gl_widget.player.animation:
                glLoadIdentity()
                glTranslatef(camera_x, camera_y, 0)
                glScalef(render_scale, render_scale, 1.0)
                if apply_centering and self.gl_widget.player.animation.centered:
                    glTranslatef(width / 2, height / 2, 0)
                self.gl_widget.render_all_layers(self.gl_widget.player.current_time)
            glReadBuffer(GL_COLOR_ATTACHMENT0)
            pixels = glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE)
            image = Image.frombytes('RGBA', (width, height), pixels).transpose(Image.FLIP_TOP_BOTTOM)
            image = self._unpremultiply_image(image)
            if background_color:
                image = self._composite_background(image, background_color)
            return image
        except Exception as e:
            self.log_widget.log(f"Error rendering frame: {e}", "ERROR")
            import traceback
            traceback.print_exc()
            return None
        finally:
            target_fbo = default_fbo if default_fbo is not None else 0
            glBindFramebuffer(GL_FRAMEBUFFER, target_fbo)
            if fbo is not None:
                glDeleteFramebuffers(1, [fbo])
            if texture is not None:
                glDeleteTextures(1, [texture])
            if projection_pushed:
                glMatrixMode(GL_PROJECTION)
                glPopMatrix()
            if modelview_pushed:
                glMatrixMode(GL_MODELVIEW)
                glPopMatrix()
            if viewport_before is not None:
                glViewport(*viewport_before)
            else:
                glViewport(0, 0, self.gl_widget.width(), self.gl_widget.height())
            self.gl_widget.doneCurrent()
            self.gl_widget.update()

    def _find_sprite_in_atlases(self, sprite_name: str):
        """Return (sprite, atlas) for a sprite name."""
        for atlas in self.gl_widget.texture_atlases:
            sprite = atlas.get_sprite(sprite_name)
            if sprite:
                return sprite, atlas
        return None, None

    def _compute_frame_bounds(self, time: float, include_hidden: bool = False) -> Optional[Tuple[float, float, float, float]]:
        """
        Compute world-space bounds for all visible layers at a specific time.
        """
        animation = self.gl_widget.player.animation
        if not animation:
            return None

        layer_states = self.gl_widget._build_layer_world_states(anim_time=time)
        renderer = self.gl_widget.renderer

        min_x = math.inf
        min_y = math.inf
        max_x = -math.inf
        max_y = -math.inf
        any_layer = False

        for layer in animation.layers:
            if not include_hidden and not layer.visible:
                continue
            state = layer_states.get(layer.layer_id)
            if not state:
                continue

            sprite_name = state.get('sprite_name')
            if not sprite_name:
                continue

            sprite, atlas = self._find_sprite_in_atlases(sprite_name)
            if not sprite or not atlas:
                continue

            local_vertices = renderer.compute_local_vertices(sprite, atlas)
            if not local_vertices:
                continue
            user_offset_x, user_offset_y = self.gl_widget.layer_offsets.get(layer.layer_id, (0, 0))

            m00 = state['m00']
            m01 = state['m01']
            m10 = state['m10']
            m11 = state['m11']
            tx = state['tx'] + user_offset_x
            ty = state['ty'] + user_offset_y

            for lx, ly in local_vertices:
                wx = m00 * lx + m01 * ly + tx
                wy = m10 * lx + m11 * ly + ty
                min_x = min(min_x, wx)
                min_y = min(min_y, wy)
                max_x = max(max_x, wx)
                max_y = max(max_y, wy)
                any_layer = True

        if not any_layer:
            return None

        return (min_x, min_y, max_x, max_y)

    def _merge_bounds(
        self,
        existing: Optional[Tuple[float, float, float, float]],
        new_bounds: Optional[Tuple[float, float, float, float]]
    ) -> Optional[Tuple[float, float, float, float]]:
        if not new_bounds:
            return existing
        if not existing:
            return new_bounds
        min_x = min(existing[0], new_bounds[0])
        min_y = min(existing[1], new_bounds[1])
        max_x = max(existing[2], new_bounds[2])
        max_y = max(existing[3], new_bounds[3])
        return (min_x, min_y, max_x, max_y)

    def _compute_animation_bounds(self, fps: float, include_hidden: bool = False) -> Optional[Tuple[float, float, float, float]]:
        """
        Compute aggregate bounds for the entire animation by sampling each frame at the export FPS.
        """
        animation = self.gl_widget.player.animation
        if not animation or fps <= 0.0:
            return None

        duration = self.gl_widget.player.duration
        total_frames = max(1, int(math.ceil(duration * fps)))
        aggregated = None

        for frame_index in range(total_frames + 1):
            frame_time = min(duration, frame_index / fps)
            bounds = self._compute_frame_bounds(frame_time, include_hidden)
            aggregated = self._merge_bounds(aggregated, bounds)

        return aggregated

    @staticmethod
    def _unpremultiply_image(image: Image.Image) -> Image.Image:
        """Convert a premultiplied-alpha image to straight alpha."""
        if image.mode != 'RGBA':
            return image
        arr = np.array(image, dtype=np.float32)
        alpha = arr[..., 3:4]
        mask = alpha > 0.0
        safe_alpha = np.where(mask, alpha, 1.0)
        arr[..., :3] = np.where(mask, arr[..., :3] * 255.0 / safe_alpha, 0.0)
        arr[..., :3] = np.clip(arr[..., :3], 0.0, 255.0)
        return Image.fromarray(arr.astype(np.uint8), 'RGBA')

    @staticmethod
    def _composite_background(image: Image.Image, color: Tuple[int, int, int, int]) -> Image.Image:
        """Composite an RGBA image over an opaque background color."""
        if image.mode != 'RGBA':
            return image
        base = Image.new('RGBA', image.size, color)
        return Image.alpha_composite(base, image)

    def _create_unique_export_folder(self, root: str, base_name: str) -> str:
        """Create a unique directory inside root with base_name."""
        safe_name = re.sub(r'[^0-9a-zA-Z_-]+', '_', base_name).strip('_') or "animation"
        root = os.path.abspath(root)
        os.makedirs(root, exist_ok=True)

        candidate = os.path.join(root, safe_name)
        counter = 1
        while os.path.exists(candidate):
            candidate = os.path.join(root, f"{safe_name}_{counter:02d}")
            counter += 1
        os.makedirs(candidate, exist_ok=True)
        return candidate

    def _get_full_resolution_scale(self) -> float:
        """
        Determine the multiplier needed to restore native sprite resolution.

        Returns 2.0 if any loaded atlas is marked as hi-res (downscaled by 0.5),
        otherwise returns 1.0.
        """
        scale = 1.0
        for atlas in self.gl_widget.texture_atlases:
            if atlas.is_hires:
                scale = max(scale, 2.0)
        return scale
    
    def export_current_frame(self):
        """Export current frame as transparent PNG"""
        if not self.gl_widget.player.animation:
            QMessageBox.warning(self, "Error", "No animation loaded")
            return
        
        export_params = self._compute_png_export_params()
        if not export_params:
            QMessageBox.warning(self, "Error", "No animation loaded")
            return
        export_width, export_height, camera_override, render_scale_override, apply_centering = export_params
        if camera_override:
            self.log_widget.log(
                f"PNG full-resolution bounds: {export_width}x{export_height}", "INFO"
            )
        
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export Frame", "", "PNG Image (*.png)"
        )
        
        if filename:
            try:
                background_color = self._active_background_color()
                image = self.render_frame_to_image(
                    export_width,
                    export_height,
                    camera_override=camera_override,
                    render_scale_override=render_scale_override,
                    apply_centering=apply_centering,
                    background_color=background_color,
                )
                
                if image:
                    image.save(filename, 'PNG')
                    self.log_widget.log(f"Frame exported to: {filename}", "SUCCESS")
                else:
                    self.log_widget.log("Failed to render frame", "ERROR")
            
            except Exception as e:
                self.log_widget.log(f"Error exporting frame: {e}", "ERROR")
                import traceback
                traceback.print_exc()

    def export_animation_frames_as_png(self):
        """Export every frame of the current animation as PNG files."""
        if not self.gl_widget.player.animation:
            QMessageBox.warning(self, "Error", "No animation loaded")
            return

        target_dir = QFileDialog.getExistingDirectory(
            self, "Select Destination Folder", ""
        )
        if not target_dir:
            return

        export_params = self._compute_png_export_params()
        if not export_params:
            QMessageBox.warning(self, "Error", "Unable to prepare export settings")
            return
        width, height, camera_override, render_scale_override, apply_centering = export_params

        animation_name = self.gl_widget.player.animation.name or "animation"
        sanitized_name = re.sub(r'[^0-9a-zA-Z_-]+', '_', animation_name).strip('_') or "animation"
        export_root = self._create_unique_export_folder(target_dir, sanitized_name)

        fps = float(max(1, self.control_panel.fps_spin.value()))
        real_duration = self._get_export_real_duration()
        total_frames = max(1, int(real_duration * fps))

        progress = QProgressDialog("Exporting frames...", "Cancel", 0, total_frames, self)
        progress.setWindowTitle("PNG Frames Export")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()

        original_time = self.gl_widget.player.current_time
        original_playing = self.gl_widget.player.playing
        self.gl_widget.player.playing = False

        exported = 0
        try:
            background_color = self._active_background_color()
            for frame_idx in range(total_frames):
                if progress.wasCanceled():
                    self.log_widget.log("Frame export cancelled by user", "WARNING")
                    break

                frame_time = self._get_export_frame_time(frame_idx, fps)
                self.gl_widget.player.current_time = frame_time
                image = self.render_frame_to_image(
                    width,
                    height,
                    camera_override=camera_override,
                    render_scale_override=render_scale_override,
                    apply_centering=apply_centering,
                    background_color=background_color,
                )
                if image:
                    filename = os.path.join(
                        export_root, f"{sanitized_name}_{frame_idx + 1:05d}.png"
                    )
                    image.save(filename, "PNG")
                    exported += 1
                else:
                    self.log_widget.log(f"Failed to render frame {frame_idx}", "WARNING")

                progress.setValue(frame_idx + 1)
                progress.setLabelText(f"Rendering frame {frame_idx + 1} of {total_frames}...")
                QApplication.processEvents()
        finally:
            progress.close()
            self.gl_widget.player.current_time = original_time
            self.gl_widget.player.playing = original_playing
            self.gl_widget.update()
            self._sync_audio_playback(original_playing)

        if exported > 0:
            QMessageBox.information(
                self,
                "Frames Exported",
                f"Saved {exported} frames to:\n{export_root}"
            )
            self.log_widget.log(
                f"PNG frames exported to {export_root} ({exported} files)", "SUCCESS"
            )
        else:
            shutil.rmtree(export_root, ignore_errors=True)
            QMessageBox.warning(self, "Export Aborted", "No frames were exported.")
    
    def export_as_psd(self):
        """Export current frame as PSD with individual sprite layers"""
        if not self.gl_widget.player.animation:
            QMessageBox.warning(self, "Error", "No animation loaded")
            return
        
        # Check for pytoshop
        pytoshop_module = self._ensure_pytoshop_available()
        if pytoshop_module is None:
            return
        psd_layers = pytoshop_module.layers
        ColorMode = pytoshop_module.enums.ColorMode
        GenericTaggedBlock = pytoshop_module.tagged_block.GenericTaggedBlock
        PsdFile = pytoshop_module.PsdFile
        packbits_ready = self._ensure_packbits_available()
        
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export PSD", "", "Photoshop Document (*.psd)"
        )
        
        if not filename:
            return
        
        # Ensure .psd extension
        if not filename.lower().endswith('.psd'):
            filename += '.psd'
        
        self._start_hang_watchdog("export_psd", timeout=15.0)
        try:
            
            animation = self.gl_widget.player.animation
            current_time = self.gl_widget.player.current_time
            include_hidden = self.export_settings.psd_include_hidden
            scale_percent = max(25, min(400, getattr(self.export_settings, 'psd_scale', 100)))
            export_scale = scale_percent / 100.0
            quality_mode = (getattr(self.export_settings, 'psd_quality', 'balanced') or 'balanced').lower()
            compression_mode = (getattr(self.export_settings, 'psd_compression', 'rle') or 'rle').lower()
            if compression_mode == 'rle' and not packbits_ready:
                self.log_widget.log(
                    "PackBits compression unavailable; falling back to RAW PSD channels.",
                    "WARNING"
                )
                compression_mode = 'raw'
            crop_canvas = getattr(self.export_settings, 'psd_crop_canvas', False)
            match_viewport = getattr(self.export_settings, 'psd_match_viewport', False)
            preserve_full_res = getattr(self.export_settings, 'psd_preserve_resolution', False)
            full_res_multiplier = max(1.0, float(getattr(self.export_settings, 'psd_full_res_multiplier', 1.0)))
            full_res_scale = self._get_full_resolution_scale() if preserve_full_res else 1.0
            native_scale_factor = full_res_scale * full_res_multiplier if preserve_full_res else 1.0
            if preserve_full_res:
                match_viewport = False
                export_scale = 1.0
                scale_percent = 100
            transform_filter, resize_filter = self.get_psd_resample_filters(quality_mode)
            compression_value = 1 if compression_mode == 'rle' else 0
            render_scale_for_export = self.gl_widget.render_scale if match_viewport else 1.0
            camera_x_for_export = self.gl_widget.camera_x if match_viewport else 0.0
            camera_y_for_export = self.gl_widget.camera_y if match_viewport else 0.0
            scale_factor = render_scale_for_export * export_scale
            
            viewport_width = self.gl_widget.width()
            viewport_height = self.gl_widget.height()
            # Scaled canvas dimensions default to viewport; they may be overridden later when
            # match_viewport is disabled or when cropping trims the canvas.
            if match_viewport:
                scaled_canvas_width = max(1, int(round(max(1, viewport_width) * export_scale * native_scale_factor)))
                scaled_canvas_height = max(1, int(round(max(1, viewport_height) * export_scale * native_scale_factor)))
            else:
                scaled_canvas_width = 0
                scaled_canvas_height = 0
            
            native_info = ""
            if preserve_full_res:
                native_info = f", native_scale={native_scale_factor:.2f}x"
            self.log_widget.log(
                f"Exporting PSD (scale={scale_percent}%, quality={quality_mode}, compression={compression_mode}, "
                f"full_res={'Yes' if preserve_full_res else 'No'}{native_info})",
                "INFO"
            )
            self.log_widget.log(
                f"Include hidden layers: {'Yes' if include_hidden else 'No'}, "
                f"crop canvas: {'Yes' if crop_canvas else 'No'}, "
                f"match viewer zoom: {'Yes' if match_viewport else 'No'}",
                "INFO"
            )
            
            # Utility helpers for coordinate conversions/metadata
            def _world_to_canvas(wx: float, wy: float) -> Tuple[float, float]:
                """Map world coordinates to PSD canvas space before cropping."""
                canvas_x = wx
                canvas_y = wy
                if match_viewport and animation.centered:
                    canvas_x += viewport_width / 2
                    canvas_y += viewport_height / 2
                canvas_x = canvas_x * render_scale_for_export + camera_x_for_export
                canvas_y = canvas_y * render_scale_for_export + camera_y_for_export
                canvas_x *= export_scale
                canvas_y *= export_scale
                if preserve_full_res and native_scale_factor != 1.0:
                    canvas_x *= native_scale_factor
                    canvas_y *= native_scale_factor
                return canvas_x, canvas_y

            def _offset_polygon_canvas(layer_info: Dict, dx: float, dy: float) -> None:
                """Shift stored polygon canvas coordinates when the canvas origin changes."""
                metadata_ref = layer_info.get('metadata')
                if not metadata_ref:
                    return
                sprite_meta = metadata_ref.get('sprite')
                if not sprite_meta:
                    return
                polygon_meta = sprite_meta.get('polygon')
                if not polygon_meta:
                    return

                def _shift_points(points: Optional[List[Dict[str, float]]]) -> None:
                    if not points:
                        return
                    for point in points:
                        point['x'] -= dx
                        point['y'] -= dy

                _shift_points(polygon_meta.get('vertices_canvas'))
                segments = polygon_meta.get('segments') or []
                for segment in segments:
                    _shift_points(segment.get('canvas'))
            
            def _render_polygon_sprite_layer(
                local_vertices: List[Tuple[float, float]],
                texcoords: List[Tuple[float, float]],
                triangles: List[int],
                world_matrix: Tuple[float, float, float, float, float, float],
                atlas_pixels: np.ndarray,
                color_tint: Tuple[float, float, float],
                opacity_value: float
            ) -> Optional[Dict[str, Any]]:
                """
                Rasterize a polygon sprite into layer space using barycentric sampling.
                Returns None if data is invalid.
                """
                if (
                    not local_vertices
                    or not texcoords
                    or not triangles
                    or len(local_vertices) != len(texcoords)
                    or atlas_pixels is None
                ):
                    return None
                
                m00, m01, m10, m11, tx, ty = world_matrix
                atlas_height, atlas_width = atlas_pixels.shape[:2]
                texcoords_px = [
                    (u * atlas_width, v * atlas_height)
                    for u, v in texcoords
                ]
                
                world_vertices = []
                canvas_vertices = []
                for lvx, lvy in local_vertices:
                    wx = m00 * lvx + m01 * lvy + tx
                    wy = m10 * lvx + m11 * lvy + ty
                    world_vertices.append({'x': wx, 'y': wy})
                    cx, cy = _world_to_canvas(wx, wy)
                    canvas_vertices.append({'x': cx, 'y': cy})
                
                if not canvas_vertices:
                    return None
                
                canvas_xs = [pt['x'] for pt in canvas_vertices]
                canvas_ys = [pt['y'] for pt in canvas_vertices]
                min_canvas_x = min(canvas_xs)
                max_canvas_x = max(canvas_xs)
                min_canvas_y = min(canvas_ys)
                max_canvas_y = max(canvas_ys)
                
                width = max(1, int(math.ceil(max_canvas_x - min_canvas_x)))
                height = max(1, int(math.ceil(max_canvas_y - min_canvas_y)))
                if width <= 0 or height <= 0:
                    return None
                
                layer_buffer = np.zeros((height, width, 4), dtype=np.float32)
                vertex_layer_coords = [
                    (pt['x'] - min_canvas_x, pt['y'] - min_canvas_y)
                    for pt in canvas_vertices
                ]
                epsilon = 1e-5
                
                texcoords_count = len(texcoords_px)
                vertex_count = len(vertex_layer_coords)
                
                for tri_start in range(0, len(triangles), 3):
                    idx0 = triangles[tri_start]
                    idx1 = triangles[tri_start + 1] if tri_start + 1 < len(triangles) else None
                    idx2 = triangles[tri_start + 2] if tri_start + 2 < len(triangles) else None
                    if (
                        idx0 is None or idx1 is None or idx2 is None
                        or idx0 >= vertex_count or idx1 >= vertex_count or idx2 >= vertex_count
                        or idx0 >= texcoords_count or idx1 >= texcoords_count or idx2 >= texcoords_count
                        or idx0 < 0 or idx1 < 0 or idx2 < 0
                    ):
                        continue
                    
                    (dx0, dy0) = vertex_layer_coords[idx0]
                    (dx1, dy1) = vertex_layer_coords[idx1]
                    (dx2, dy2) = vertex_layer_coords[idx2]
                    (sx0, sy0) = texcoords_px[idx0]
                    (sx1, sy1) = texcoords_px[idx1]
                    (sx2, sy2) = texcoords_px[idx2]
                    
                    tri_min_x = max(0, int(math.floor(min(dx0, dx1, dx2))))
                    tri_max_x = min(width, int(math.ceil(max(dx0, dx1, dx2))))
                    tri_min_y = max(0, int(math.floor(min(dy0, dy1, dy2))))
                    tri_max_y = min(height, int(math.ceil(max(dy0, dy1, dy2))))
                    
                    if tri_max_x <= tri_min_x or tri_max_y <= tri_min_y:
                        continue
                    
                    xs = np.arange(tri_min_x, tri_max_x)
                    ys = np.arange(tri_min_y, tri_max_y)
                    if xs.size == 0 or ys.size == 0:
                        continue
                    
                    grid_x_int, grid_y_int = np.meshgrid(xs, ys)
                    grid_x = grid_x_int.astype(np.float32)
                    grid_y = grid_y_int.astype(np.float32)
                    sample_x = grid_x + 0.5
                    sample_y = grid_y + 0.5
                    
                    denom = (dy1 - dy2) * (dx0 - dx2) + (dx2 - dx1) * (dy0 - dy2)
                    if abs(denom) < epsilon:
                        continue
                    
                    w0 = ((dy1 - dy2) * (sample_x - dx2) + (dx2 - dx1) * (sample_y - dy2)) / denom
                    w1 = ((dy2 - dy0) * (sample_x - dx2) + (dx0 - dx2) * (sample_y - dy2)) / denom
                    w2 = 1.0 - w0 - w1
                    mask = (w0 >= -epsilon) & (w1 >= -epsilon) & (w2 >= -epsilon)
                    if not np.any(mask):
                        continue
                    
                    mask_idx = np.nonzero(mask)
                    dest_x_vals = grid_x_int[mask_idx]
                    dest_y_vals = grid_y_int[mask_idx]
                    w0_vals = w0[mask_idx]
                    w1_vals = w1[mask_idx]
                    w2_vals = w2[mask_idx]
                    
                    src_x_vals = w0_vals * sx0 + w1_vals * sx1 + w2_vals * sx2
                    src_y_vals = w0_vals * sy0 + w1_vals * sy1 + w2_vals * sy2
                    
                    src_x_vals = np.clip(src_x_vals, 0.0, atlas_width - 1.0)
                    src_y_vals = np.clip(src_y_vals, 0.0, atlas_height - 1.0)
                    
                    x0_idx = np.floor(src_x_vals).astype(np.int32)
                    y0_idx = np.floor(src_y_vals).astype(np.int32)
                    x1_idx = np.clip(x0_idx + 1, 0, atlas_width - 1)
                    y1_idx = np.clip(y0_idx + 1, 0, atlas_height - 1)
                    
                    wx = (src_x_vals - x0_idx).astype(np.float32)
                    wy = (src_y_vals - y0_idx).astype(np.float32)
                    
                    top = atlas_pixels[y0_idx, x0_idx] * (1.0 - wx)[:, None] + atlas_pixels[y0_idx, x1_idx] * wx[:, None]
                    bottom = atlas_pixels[y1_idx, x0_idx] * (1.0 - wx)[:, None] + atlas_pixels[y1_idx, x1_idx] * wx[:, None]
                    samples = top * (1.0 - wy)[:, None] + bottom * wy[:, None]
                    
                    layer_buffer[dest_y_vals, dest_x_vals] = samples
                
                if color_tint:
                    tint_r, tint_g, tint_b = color_tint
                    if tint_r != 1.0:
                        layer_buffer[:, :, 0] *= tint_r
                    if tint_g != 1.0:
                        layer_buffer[:, :, 1] *= tint_g
                    if tint_b != 1.0:
                        layer_buffer[:, :, 2] *= tint_b
                if opacity_value < 1.0:
                    layer_buffer[:, :, 3] *= opacity_value
                
                layer_bytes = np.clip(layer_buffer, 0.0, 1.0)
                layer_bytes = (layer_bytes * 255.0 + 0.5).astype(np.uint8)
                layer_image = Image.fromarray(layer_bytes, 'RGBA')
                return {
                    'image': layer_image,
                    'origin_x': min_canvas_x,
                    'origin_y': min_canvas_y,
                    'canvas_vertices': canvas_vertices,
                    'world_vertices': world_vertices
                }

            # Build layer map and calculate world states
            layer_map = {layer.layer_id: layer for layer in animation.layers}
            layer_world_states = {}
            
            for layer in animation.layers:
                state = self.gl_widget.renderer.calculate_world_state(
                    layer, current_time, self.gl_widget.player, layer_map, 
                    layer_world_states, self.gl_widget.texture_atlases
                )
                layer_world_states[layer.layer_id] = self.gl_widget.apply_user_transforms(
                    layer.layer_id, state
                )
            
            # Cache loaded atlas images and pixel arrays
            atlas_images = {}
            atlas_pixel_arrays = {}
            
            # Collect layer data for PSD
            psd_layer_data = []
            
            # Process layers in reverse order (back to front, like rendering)
            for layer in reversed(animation.layers):
                if not include_hidden and not layer.visible:
                    continue
                
                world_state = layer_world_states[layer.layer_id]
                sprite_name = world_state['sprite_name']
                
                if not sprite_name:
                    continue
                
                # Find sprite in atlases
                sprite = None
                atlas = None
                for atl in self.gl_widget.texture_atlases:
                    sprite = atl.get_sprite(sprite_name)
                    if sprite:
                        atlas = atl
                        break
                
                if not sprite or not atlas:
                    continue
                
                # Load atlas image if not cached
                if atlas.image_path not in atlas_images:
                    try:
                        atlas_img = Image.open(atlas.image_path)
                        atlas_img = atlas_img.convert('RGBA')
                        atlas_images[atlas.image_path] = atlas_img
                        atlas_pixel_arrays[atlas.image_path] = np.asarray(atlas_img, dtype=np.float32) / 255.0
                    except Exception as e:
                        self.log_widget.log(f"Failed to load atlas: {e}", "WARNING")
                        continue
                elif atlas.image_path not in atlas_pixel_arrays:
                    atlas_pixel_arrays[atlas.image_path] = np.asarray(atlas_images[atlas.image_path], dtype=np.float32) / 255.0
                
                atlas_img = atlas_images[atlas.image_path]
                atlas_pixels = atlas_pixel_arrays.get(atlas.image_path)
                
                # Extract sprite from atlas for quad-based fallback rendering
                sprite_img = atlas_img.crop((
                    sprite.x, sprite.y,
                    sprite.x + sprite.w, sprite.y + sprite.h
                ))
                if sprite.rotated:
                    sprite_img = sprite_img.rotate(90, expand=True)
                orig_sprite_w, orig_sprite_h = sprite_img.size
                hires_scale = 0.5 if atlas.is_hires else 1.0
                
                # Transformation matrix and color information
                m00 = world_state['m00']
                m01 = world_state['m01']
                m10 = world_state['m10']
                m11 = world_state['m11']
                tx = world_state['tx']
                ty = world_state['ty']
                opacity = world_state['world_opacity']
                r = world_state['r'] / 255.0
                g = world_state['g'] / 255.0
                b = world_state['b'] / 255.0
                
                # Apply user offsets
                user_offset_x, user_offset_y = self.gl_widget.layer_offsets.get(layer.layer_id, (0, 0))
                tx += user_offset_x
                ty += user_offset_y
                
                # Attempt polygon-aware rasterization if geometry is available
                polygon_local_vertices: List[Tuple[float, float]] = []
                polygon_texcoords: List[Tuple[float, float]] = []
                polygon_triangles: List[int] = []
                polygon_world_vertices: List[Dict[str, float]] = []
                polygon_canvas_vertices: List[Dict[str, float]] = []
                polygon_render_result: Optional[Dict[str, Any]] = None
                
                if sprite.has_polygon_mesh:
                    try:
                        geometry = self.gl_widget.renderer._build_polygon_geometry(sprite, atlas)
                    except Exception as geom_exc:  # pragma: no cover - defensive
                        self.log_widget.log(
                            f"Failed to build polygon geometry for {layer.name}: {geom_exc}",
                            "WARNING"
                        )
                        geometry = None
                    if geometry:
                        polygon_local_vertices, polygon_texcoords, polygon_triangles = geometry
                        if atlas_pixels is not None:
                            try:
                                polygon_render_result = _render_polygon_sprite_layer(
                                    polygon_local_vertices,
                                    polygon_texcoords,
                                    polygon_triangles,
                                    (m00, m01, m10, m11, tx, ty),
                                    atlas_pixels,
                                    (r, g, b),
                                    opacity
                                )
                            except Exception as raster_exc:  # pragma: no cover - defensive
                                self.log_widget.log(
                                    f"Polygon rasterization failed for {layer.name}: {raster_exc}",
                                    "WARNING"
                                )
                                polygon_render_result = None
                        if polygon_render_result:
                            polygon_world_vertices = polygon_render_result['world_vertices']
                            polygon_canvas_vertices = polygon_render_result['canvas_vertices']
                
                transformed_img = None
                final_x = 0.0
                final_y = 0.0
                polygon_layer_used = polygon_render_result is not None
                
                if polygon_layer_used:
                    transformed_img = polygon_render_result['image']
                    final_x = polygon_render_result['origin_x']
                    final_y = polygon_render_result['origin_y']
                else:
                    # Fall back to quad-based affine transform rendering
                    trim_multiplier = self.gl_widget.renderer.trim_shift_multiplier
                    sprite_offset_x = sprite.offset_x * hires_scale * trim_multiplier * self.gl_widget.position_scale
                    sprite_offset_y = sprite.offset_y * hires_scale * trim_multiplier * self.gl_widget.position_scale
                    scaled_w = orig_sprite_w * hires_scale * self.gl_widget.position_scale
                    scaled_h = orig_sprite_h * hires_scale * self.gl_widget.position_scale
                    corners_local = [
                        (sprite_offset_x, sprite_offset_y),
                        (sprite_offset_x + scaled_w, sprite_offset_y),
                        (sprite_offset_x + scaled_w, sprite_offset_y + scaled_h),
                        (sprite_offset_x, sprite_offset_y + scaled_h),
                    ]
                    corners_world = []
                    for lx, ly in corners_local:
                        wx = m00 * lx + m01 * ly + tx
                        wy = m10 * lx + m11 * ly + ty
                        corners_world.append((wx, wy))
                    world_xs = [c[0] for c in corners_world]
                    world_ys = [c[1] for c in corners_world]
                    world_min_x = min(world_xs)
                    world_max_x = max(world_xs)
                    world_min_y = min(world_ys)
                    world_max_y = max(world_ys)
                    bbox_w = int(math.ceil(world_max_x - world_min_x))
                    bbox_h = int(math.ceil(world_max_y - world_min_y))
                    if bbox_w <= 0 or bbox_h <= 0:
                        continue
                    det = m00 * m11 - m01 * m10
                    if abs(det) < 1e-10:
                        continue
                    inv_m00 = m11 / det
                    inv_m01 = -m01 / det
                    inv_m10 = -m10 / det
                    inv_m11 = m00 / det
                    offset_x = world_min_x - tx
                    offset_y = world_min_y - ty
                    inv_tx = inv_m00 * offset_x + inv_m01 * offset_y
                    inv_ty = inv_m10 * offset_x + inv_m11 * offset_y
                    inv_tx -= sprite_offset_x
                    inv_ty -= sprite_offset_y
                    scale_to_img_x = orig_sprite_w / scaled_w if scaled_w > 0 else 1
                    scale_to_img_y = orig_sprite_h / scaled_h if scaled_h > 0 else 1
                    final_a = inv_m00 * scale_to_img_x
                    final_b = inv_m01 * scale_to_img_x
                    final_c = inv_tx * scale_to_img_x
                    final_d = inv_m10 * scale_to_img_y
                    final_e = inv_m11 * scale_to_img_y
                    final_f = inv_ty * scale_to_img_y
                    image_scale = scale_factor
                    if preserve_full_res:
                        image_scale *= native_scale_factor
                    if image_scale <= 0:
                        image_scale = 1.0
                    target_w = max(1, int(math.ceil(bbox_w * image_scale)))
                    target_h = max(1, int(math.ceil(bbox_h * image_scale)))
                    transformed_img = sprite_img.transform(
                        (target_w, target_h),
                        Image.Transform.AFFINE,
                        (
                            final_a / image_scale,
                            final_b / image_scale,
                            final_c,
                            final_d / image_scale,
                            final_e / image_scale,
                            final_f
                        ),
                        resample=transform_filter
                    )
                    if r != 1.0 or g != 1.0 or b != 1.0:
                        img_r, img_g, img_b, img_a = transformed_img.split()
                        img_r = img_r.point(lambda x: int(x * r))
                        img_g = img_g.point(lambda x: int(x * g))
                        img_b = img_b.point(lambda x: int(x * b))
                        transformed_img = Image.merge('RGBA', (img_r, img_g, img_b, img_a))
                    if opacity < 1.0:
                        img_r, img_g, img_b, img_a = transformed_img.split()
                        img_a = img_a.point(lambda x: int(x * opacity))
                        transformed_img = Image.merge('RGBA', (img_r, img_g, img_b, img_a))
                    final_x = world_min_x
                    final_y = world_min_y
                    if match_viewport and animation.centered:
                        final_x += viewport_width / 2
                        final_y += viewport_height / 2
                    final_x = final_x * render_scale_for_export + camera_x_for_export
                    final_y = final_y * render_scale_for_export + camera_y_for_export
                    final_x *= export_scale
                    final_y *= export_scale
                    if preserve_full_res and native_scale_factor != 1.0:
                        final_x *= native_scale_factor
                        final_y *= native_scale_factor
                
                if transformed_img is None:
                    continue
                
                # Store layer data
                psd_blend_mode = self._map_psd_blend_mode(layer.blend_mode)
                atlas_rel = atlas.image_path
                if self.game_path:
                    try:
                        atlas_rel = os.path.relpath(atlas.image_path, os.path.join(self.game_path, "data"))
                    except Exception:
                        pass
                metadata: Dict[str, Any] = {
                    'blend_mode': {
                        'engine_id': layer.blend_mode,
                        'engine_label': self._describe_engine_blend_mode(layer.blend_mode),
                        'psd_mode': psd_blend_mode.name if psd_blend_mode else None
                    },
                    'sprite': {
                        'name': sprite.name,
                        'atlas_path': atlas_rel,
                        'rect': {'x': sprite.x, 'y': sprite.y, 'w': sprite.w, 'h': sprite.h},
                        'offset': {'x': sprite.offset_x, 'y': sprite.offset_y},
                        'original_size': {'w': sprite.original_w, 'h': sprite.original_h},
                        'rotated': sprite.rotated,
                        'has_polygon_mesh': sprite.has_polygon_mesh
                    }
                }
                if sprite.has_polygon_mesh:
                    atlas_w = atlas.image_width or (atlas_img.width if atlas_img else 0)
                    atlas_h = atlas.image_height or (atlas_img.height if atlas_img else 0)
                    polygon_meta: Dict[str, Any] = {
                        'vertices': sprite.vertices,
                        'vertices_uv': sprite.vertices_uv,
                        'triangles': sprite.triangles,
                        'vertex_space': 'trimmed_local',
                        'uv_space': 'atlas_normalized'
                    }
                    if sprite.vertices_uv and atlas_w > 0 and atlas_h > 0:
                        polygon_meta['vertices_uv_pixels'] = [
                            {'u': uv_x * atlas_w, 'v': uv_y * atlas_h}
                            for uv_x, uv_y in sprite.vertices_uv
                        ]
                    renderer = self.gl_widget.renderer
                    local_vertices_for_meta: List[Tuple[float, float]] = polygon_local_vertices.copy()
                    if not local_vertices_for_meta and renderer and hasattr(renderer, 'compute_local_vertices'):
                        try:
                            local_vertices_for_meta = renderer.compute_local_vertices(sprite, atlas)
                        except Exception as vertex_exc:  # pragma: no cover - defensive
                            self.log_widget.log(
                                f"Failed to derive polygon vertices for {layer.name}: {vertex_exc}",
                                "WARNING"
                            )
                            local_vertices_for_meta = []
                    if local_vertices_for_meta:
                        polygon_meta['local_vertices_scaled'] = local_vertices_for_meta
                    if not polygon_world_vertices and local_vertices_for_meta:
                        computed_world: List[Dict[str, float]] = []
                        computed_canvas: List[Dict[str, float]] = []
                        for lvx, lvy in local_vertices_for_meta:
                            world_x = m00 * lvx + m01 * lvy + tx
                            world_y = m10 * lvx + m11 * lvy + ty
                            canvas_x, canvas_y = _world_to_canvas(world_x, world_y)
                            computed_world.append({'x': world_x, 'y': world_y})
                            computed_canvas.append({'x': canvas_x, 'y': canvas_y})
                        polygon_world_vertices = computed_world
                        polygon_canvas_vertices = computed_canvas
                    if polygon_world_vertices:
                        polygon_meta['vertices_world'] = [dict(pt) for pt in polygon_world_vertices]
                    if polygon_canvas_vertices:
                        polygon_meta['vertices_canvas'] = [dict(pt) for pt in polygon_canvas_vertices]
                    if (
                        local_vertices_for_meta
                        and polygon_world_vertices
                        and polygon_canvas_vertices
                    ):
                        segments: List[Dict[str, Any]] = []
                        vertex_count = len(local_vertices_for_meta)
                        uv_entries = sprite.vertices_uv or []
                        uv_pixels = polygon_meta.get('vertices_uv_pixels') or []
                        triangles = sprite.triangles or []
                        for tri_start in range(0, len(triangles), 3):
                            tri_indices = triangles[tri_start:tri_start + 3]
                            if len(tri_indices) < 3:
                                continue
                            if any(idx < 0 or idx >= vertex_count for idx in tri_indices):
                                continue
                            seg_entry: Dict[str, Any] = {
                                'indices': tri_indices,
                                'world': [dict(polygon_world_vertices[idx]) for idx in tri_indices],
                                'canvas': [dict(polygon_canvas_vertices[idx]) for idx in tri_indices]
                            }
                            if uv_entries and all(idx < len(uv_entries) for idx in tri_indices):
                                seg_entry['uv_normalized'] = [
                                    {'u': uv_entries[idx][0], 'v': uv_entries[idx][1]}
                                    for idx in tri_indices
                                ]
                            if uv_pixels and all(idx < len(uv_pixels) for idx in tri_indices):
                                seg_entry['uv_pixels'] = [
                                    {'u': uv_pixels[idx]['u'], 'v': uv_pixels[idx]['v']}
                                    for idx in tri_indices
                                ]
                            segments.append(seg_entry)
                        if segments:
                            polygon_meta['segments'] = segments
                    metadata['sprite']['polygon'] = polygon_meta
                psd_layer_data.append({
                    'name': layer.name,
                    'image': transformed_img,
                    'x': int(round(final_x)),
                    'y': int(round(final_y)),
                    'opacity': int(max(0.0, min(1.0, opacity)) * 255),
                    'width': transformed_img.width,
                    'height': transformed_img.height,
                    'blend_mode': layer.blend_mode,
                    'psd_blend_mode': psd_blend_mode,
                    'metadata': metadata
                })
            
            self.log_widget.log(f"Processed {len(psd_layer_data)} visible layers", "INFO")
            
            # Adjust canvas bounds
            crop_left = 0
            crop_top = 0
            if psd_layer_data:
                content_left = min(layer_info['x'] for layer_info in psd_layer_data)
                content_top = min(layer_info['y'] for layer_info in psd_layer_data)
                content_right = max(layer_info['x'] + layer_info['width'] for layer_info in psd_layer_data)
                content_bottom = max(layer_info['y'] + layer_info['height'] for layer_info in psd_layer_data)
                
                if not match_viewport:
                    # Always expand to include the full sprite content regardless of camera zoom
                    crop_left = int(math.floor(content_left))
                    crop_top = int(math.floor(content_top))
                    scaled_canvas_width = max(1, int(math.ceil(content_right - content_left)))
                    scaled_canvas_height = max(1, int(math.ceil(content_bottom - content_top)))
                    
                    for layer_info in psd_layer_data:
                        layer_info['x'] -= crop_left
                        layer_info['y'] -= crop_top
                        _offset_polygon_canvas(layer_info, crop_left, crop_top)
                    
                    self.log_widget.log(
                        f"Canvas set to full content bounds: {scaled_canvas_width}x{scaled_canvas_height}", "INFO"
                    )
                elif crop_canvas:
                    visible_left = scaled_canvas_width
                    visible_top = scaled_canvas_height
                    visible_right = 0
                    visible_bottom = 0
                    
                    for layer_info in psd_layer_data:
                        left = max(0, min(scaled_canvas_width, layer_info['x']))
                        top = max(0, min(scaled_canvas_height, layer_info['y']))
                        right = max(left, min(scaled_canvas_width, layer_info['x'] + layer_info['width']))
                        bottom = max(top, min(scaled_canvas_height, layer_info['y'] + layer_info['height']))
                        
                        if right <= left or bottom <= top:
                            continue
                        
                        visible_left = min(visible_left, left)
                        visible_top = min(visible_top, top)
                        visible_right = max(visible_right, right)
                        visible_bottom = max(visible_bottom, bottom)
                    
                    if visible_right > visible_left and visible_bottom > visible_top:
                        crop_left = int(visible_left)
                        crop_top = int(visible_top)
                        scaled_canvas_width = max(1, int(visible_right - visible_left))
                        scaled_canvas_height = max(1, int(visible_bottom - visible_top))
                        
                        for layer_info in psd_layer_data:
                            layer_info['x'] -= crop_left
                            layer_info['y'] -= crop_top
                            _offset_polygon_canvas(layer_info, crop_left, crop_top)
                        
                        self.log_widget.log(
                            f"Cropped PSD canvas to {scaled_canvas_width}x{scaled_canvas_height}", "INFO"
                        )
            
            if scaled_canvas_width <= 0 or scaled_canvas_height <= 0:
                scaled_canvas_width = max(1, int(round(max(1, viewport_width) * export_scale * (native_scale_factor if preserve_full_res else 1.0))))
                scaled_canvas_height = max(1, int(round(max(1, viewport_height) * export_scale * (native_scale_factor if preserve_full_res else 1.0))))
            
            self.log_widget.log(
                f"Final PSD canvas: {scaled_canvas_width}x{scaled_canvas_height}", "INFO"
            )

            bg_color = self._active_background_color()
            if bg_color:
                background_layer = Image.new('RGBA', (scaled_canvas_width, scaled_canvas_height), bg_color)
                psd_layer_data.insert(0, {
                    'name': "Background Color",
                    'image': background_layer,
                    'x': 0,
                    'y': 0,
                    'opacity': 255,
                    'width': scaled_canvas_width,
                    'height': scaled_canvas_height,
                    'blend_mode': BlendMode.STANDARD,
                    'psd_blend_mode': self._map_psd_blend_mode(BlendMode.STANDARD),
                    'metadata': {'background_fill': True, 'color': {'r': bg_color[0], 'g': bg_color[1], 'b': bg_color[2], 'a': bg_color[3]}},
                })
            
            # Determine color mode enum (older pytoshop versions use lowercase names)
            psd_color_mode = getattr(ColorMode, 'RGB', None)
            if psd_color_mode is None:
                psd_color_mode = getattr(ColorMode, 'rgb', None)
            if psd_color_mode is None:
                try:
                    psd_color_mode = ColorMode(3)  # RGB fallback
                except Exception:
                    psd_color_mode = None
            
            psd_kwargs = dict(
                num_channels=4,
                height=scaled_canvas_height,
                width=scaled_canvas_width
            )
            if psd_color_mode is not None:
                psd_kwargs['color_mode'] = psd_color_mode
            
            # Create PSD file using pytoshop
            psd = PsdFile(**psd_kwargs)
            
            # Add each layer
            for layer_info in psd_layer_data:
                img = layer_info['image']
                x = layer_info['x']
                y = layer_info['y']
                name = layer_info['name']
                layer_opacity = layer_info['opacity']
                
                # Convert PIL image to numpy array
                img_array = np.array(img)
                
                # Get layer dimensions
                layer_h, layer_w = img_array.shape[:2]
                
                # Calculate layer bounds (clipped to canvas)
                left = max(0, x)
                top = max(0, y)
                right = min(scaled_canvas_width, x + layer_w)
                bottom = min(scaled_canvas_height, y + layer_h)
                
                # Skip if layer is completely outside canvas
                if left >= right or top >= bottom:
                    continue
                
                # Calculate the portion of the image that's visible
                img_left = left - x
                img_top = top - y
                img_right = img_left + (right - left)
                img_bottom = img_top + (bottom - top)
                
                # Crop image to visible portion
                visible_img = img_array[img_top:img_bottom, img_left:img_right]
                
                if visible_img.size == 0:
                    continue
                
                # Split into channels (R, G, B, A)
                if len(visible_img.shape) == 3 and visible_img.shape[2] == 4:
                    alpha_channel = visible_img[:, :, 3]
                    red_channel = visible_img[:, :, 0]
                    green_channel = visible_img[:, :, 1]
                    blue_channel = visible_img[:, :, 2]
                elif len(visible_img.shape) == 3 and visible_img.shape[2] == 3:
                    alpha_channel = np.full((visible_img.shape[0], visible_img.shape[1]), 255, dtype=np.uint8)
                    red_channel = visible_img[:, :, 0]
                    green_channel = visible_img[:, :, 1]
                    blue_channel = visible_img[:, :, 2]
                else:
                    continue
                
                # Create channel image data objects
                alpha_data = psd_layers.ChannelImageData(image=alpha_channel, compression=compression_value)
                red_data = psd_layers.ChannelImageData(image=red_channel, compression=compression_value)
                green_data = psd_layers.ChannelImageData(image=green_channel, compression=compression_value)
                blue_data = psd_layers.ChannelImageData(image=blue_channel, compression=compression_value)
                
                # Create layer record with channels and metadata blocks
                blend_kwargs = {}
                psd_blend_mode = layer_info.get('psd_blend_mode')
                if psd_blend_mode is not None:
                    blend_kwargs['blend_mode'] = psd_blend_mode
                blocks = []
                metadata_payload = layer_info.get('metadata')
                if metadata_payload:
                    try:
                        metadata_bytes = json.dumps(
                            metadata_payload,
                            ensure_ascii=False,
                            separators=(',', ':')
                        ).encode('utf-8')
                        blocks.append(GenericTaggedBlock(code=b'mETA', data=metadata_bytes))
                    except Exception as exc:
                        self.log_widget.log(
                            f"Failed to encode PSD metadata for {layer_info['name']}: {exc}",
                            "WARNING"
                        )
                layer_record = psd_layers.LayerRecord(
                    top=top,
                    left=left,
                    bottom=bottom,
                    right=right,
                    name=name[:31],
                    opacity=layer_opacity,
                    channels={
                        -1: alpha_data,
                        0: red_data,
                        1: green_data,
                        2: blue_data,
                    },
                    blocks=blocks or None,
                    **blend_kwargs
                )
                
                psd.layer_and_mask_info.layer_info.layer_records.append(layer_record)
            
            # Write PSD file
            with open(filename, 'wb') as f:
                psd.write(f)
            
            file_size = os.path.getsize(filename)
            self.log_widget.log(f"PSD exported to: {filename} ({file_size} bytes)", "SUCCESS")
            
            QMessageBox.information(
                self, "Export Complete",
                f"PSD exported successfully!\n\n"
                f"File: {filename}\n"
                f"Layers: {len(psd_layer_data)}\n"
                f"Size: {scaled_canvas_width}x{scaled_canvas_height}"
            )
            
        except Exception as e:
            self.log_widget.log(f"Error exporting PSD: {e}", "ERROR")
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self, "Export Error", f"Failed to export PSD: {e}")
        finally:
            self._stop_hang_watchdog()
    
    def get_psd_resample_filters(self, quality: str):
        """Return (transform_filter, resize_filter) for PSD export quality modes"""
        quality = (quality or 'balanced').lower()
        if quality == 'fast':
            return Image.Resampling.NEAREST, Image.Resampling.NEAREST
        if quality == 'high':
            return Image.Resampling.BICUBIC, Image.Resampling.BICUBIC
        if quality == 'maximum':
            # PIL transform only accepts NEAREST/BILINEAR/BICUBIC; keep transform bicubic but
            # allow final resize to use higher quality Lanczos filtering.
            return Image.Resampling.BICUBIC, Image.Resampling.LANCZOS
        # balanced / default
        return Image.Resampling.BILINEAR, Image.Resampling.BILINEAR

    def _map_psd_blend_mode(self, blend_mode: int):
        """Map internal blend modes to Photoshop equivalents."""
        module = self._ensure_pytoshop_available()
        if module is None:
            return None
        try:
            PSDBlendMode = module.enums.BlendMode
        except Exception:
            return None

        if blend_mode == BlendMode.ADDITIVE:
            return PSDBlendMode.linear_dodge
        if blend_mode == BlendMode.MULTIPLY:
            return PSDBlendMode.multiply
        if blend_mode == BlendMode.SCREEN:
            return PSDBlendMode.screen
        return PSDBlendMode.normal

    def _describe_engine_blend_mode(self, blend_mode: int) -> str:
        labels = {
            BlendMode.STANDARD: "Standard",
            BlendMode.PREMULT_ALPHA: "Premultiplied Alpha",
            BlendMode.ADDITIVE: "Additive",
            BlendMode.PREMULT_ALPHA_ALT: "Premultiplied Alpha (Alt)",
            BlendMode.PREMULT_ALPHA_ALT2: "Premultiplied Alpha (Alt2)",
            BlendMode.INHERIT: "Inherit",
            BlendMode.MULTIPLY: "Multiply",
            BlendMode.SCREEN: "Screen",
        }
        return labels.get(blend_mode, f"Unknown({blend_mode})")
    
    def _resolve_ffmpeg_path(self) -> Optional[str]:
        """Return a working FFmpeg path, updating cached value as needed."""
        stored_path = self.settings.value('ffmpeg/path', '', type=str)
        ffmpeg_path = resolve_ffmpeg_path(stored_path)

        if ffmpeg_path:
            if stored_path != ffmpeg_path:
                self.settings.setValue('ffmpeg/path', ffmpeg_path)
            return ffmpeg_path

        if stored_path:
            self.settings.remove('ffmpeg/path')
        return None

    @staticmethod
    def _ffmpeg_thread_args(max_threads: int = 0) -> List[str]:
        """Return FFmpeg thread arguments when multiple cores are available."""
        if max_threads <= 0:
            cpu_count = os.cpu_count() or 1
            max_threads = cpu_count
        thread_count = max(1, min(32, int(max_threads)))
        if thread_count <= 1:
            return []
        return ['-threads', str(thread_count)]

    def _render_video_frames(
        self,
        fps: int,
        include_audio: bool,
        use_full_res: bool,
        extra_scale: float,
        *,
        export_label: str = "Video",
    ) -> Optional[Dict[str, Any]]:
        """Render animation frames (and optional audio) for a video export."""
        animation = getattr(self.gl_widget.player, "animation", None)
        if not animation:
            QMessageBox.warning(self, "Error", "No animation loaded")
            return None

        real_duration = self._get_export_real_duration()
        total_frames = int(real_duration * fps)
        if total_frames <= 0:
            QMessageBox.warning(self, "Error", "Animation has no frames to export")
            return None

        use_full_res = bool(use_full_res)
        extra_scale = max(1.0, float(extra_scale or 1.0))
        full_res_scale = (self._get_full_resolution_scale() * extra_scale) if use_full_res else 1.0
        camera_override = None
        apply_centering = True
        render_scale_override = None
        center_x = None
        center_y = None

        if use_full_res:
            bounds = self._compute_animation_bounds(fps)
            if bounds:
                min_x, min_y, max_x, max_y = bounds
                padding = 8.0
                min_x -= padding / 2.0
                max_x += padding / 2.0
                min_y -= padding / 2.0
                max_y += padding / 2.0
                width_units = max(1e-3, max_x - min_x)
                height_units = max(1e-3, max_y - min_y)
                width = max(1, int(math.ceil(width_units * full_res_scale)))
                height = max(1, int(math.ceil(height_units * full_res_scale)))
                render_scale_override = full_res_scale
                center_x = min_x + width_units / 2.0
                center_y = min_y + height_units / 2.0
                camera_override = (
                    width * 0.5 - render_scale_override * center_x,
                    height * 0.5 - render_scale_override * center_y,
                )
                apply_centering = False
                self.log_widget.log(
                    f"{export_label} full-resolution bounds: {width}x{height}",
                    "INFO",
                )
            else:
                width = self.gl_widget.width()
                height = self.gl_widget.height()
                self.log_widget.log(
                    f"Full-resolution {export_label.lower()} bounds unavailable, using viewport dimensions.",
                    "WARNING",
                )
        else:
            width = self.gl_widget.width()
            height = self.gl_widget.height()

        width = width if width % 2 == 0 else width + 1
        height = height if height % 2 == 0 else height + 1
        if center_x is not None and render_scale_override:
            camera_override = (
                width * 0.5 - render_scale_override * center_x,
                height * 0.5 - render_scale_override * center_y,
            )

        self.log_widget.log(
            f"{export_label} export dimensions: {width}x{height} at {fps} FPS",
            "INFO",
        )

        temp_dir = tempfile.mkdtemp(prefix='msm_export_')
        self.log_widget.log(f"{export_label} temp directory: {temp_dir}", "INFO")

        original_time = self.gl_widget.player.current_time
        original_playing = self.gl_widget.player.playing
        self.gl_widget.player.playing = False

        progress = QProgressDialog(
            f"Exporting {export_label} frames...",
            "Cancel",
            0,
            total_frames,
            self,
        )
        progress.setWindowTitle("Export Progress")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()

        frame_files: List[str] = []
        was_canceled = False
        try:
            background_color = self._active_background_color()
            for frame_num in range(total_frames):
                if progress.wasCanceled():
                    was_canceled = True
                    self.log_widget.log(f"{export_label} export cancelled by user", "WARNING")
                    break

                frame_time = self._get_export_frame_time(frame_num, fps)
                self.gl_widget.player.current_time = frame_time

                image = self.render_frame_to_image(
                    width,
                    height,
                    camera_override=camera_override,
                    render_scale_override=render_scale_override,
                    apply_centering=apply_centering,
                    background_color=background_color,
                )
                if image:
                    frame_path = os.path.join(temp_dir, f"frame_{frame_num:06d}.png")
                    image.save(frame_path, 'PNG')
                    frame_files.append(frame_path)
                else:
                    self.log_widget.log(f"Failed to render frame {frame_num}", "WARNING")

                progress.setValue(frame_num + 1)
                progress.setLabelText(f"Rendering frame {frame_num + 1} of {total_frames}...")

                from PyQt6.QtWidgets import QApplication
                QApplication.processEvents()
        finally:
            progress.close()

        if was_canceled or len(frame_files) == 0:
            shutil.rmtree(temp_dir, ignore_errors=True)
            self.gl_widget.player.current_time = original_time
            self.gl_widget.player.playing = original_playing
            self.gl_widget.update()
            return None

        audio_track_path = None
        if include_audio:
            if self.audio_manager.is_ready:
                audio_speed, audio_mode = self._get_audio_export_config()
                audio_segment = self.audio_manager.export_audio_segment(
                    real_duration,
                    speed=audio_speed,
                    pitch_mode=audio_mode,
                )
                if audio_segment:
                    samples, sample_rate = audio_segment
                    audio_track_path = os.path.join(temp_dir, "audio_track.wav")
                    try:
                        sf.write(audio_track_path, samples, sample_rate)
                        audio_duration = len(samples) / sample_rate if sample_rate else 0.0
                        self.log_widget.log(
                            f"Prepared audio track ({sample_rate} Hz, {audio_duration:.2f}s) "
                            f"mode={audio_mode}, speed={audio_speed:.3f}",
                            "INFO",
                        )
                    except Exception as audio_error:
                        audio_track_path = None
                        self.log_widget.log(f"Failed to write audio track: {audio_error}", "WARNING")
                else:
                    self.log_widget.log("Audio track unavailable for export", "WARNING")
            else:
                self.log_widget.log("Audio export requested but no audio loaded", "WARNING")

        self.log_widget.log(
            f"Rendered {len(frame_files)} frames for {export_label} export, ready for encoding...",
            "INFO",
        )

        return {
            "temp_dir": temp_dir,
            "frame_files": frame_files,
            "input_pattern": os.path.join(temp_dir, "frame_%06d.png").replace('\\', '/'),
            "audio_path": audio_track_path,
            "original_time": original_time,
            "original_playing": original_playing,
            "width": width,
            "height": height,
            "fps": fps,
            "duration": real_duration,
        }

    def export_as_mov(self):
        """Export animation as transparent MOV video"""
        if not self.gl_widget.player.animation:
            QMessageBox.warning(self, "Error", "No animation loaded")
            return
        
        # Check for ffmpeg (supports auto-installed copy from settings)
        ffmpeg_path = self._resolve_ffmpeg_path()
        if not ffmpeg_path:
            QMessageBox.warning(
                self,
                "FFmpeg Required",
                "FFmpeg is required for MOV export.\n\n"
                "Use Settings > Application > FFmpeg Tools to perform the one-click install, "
                "or install FFmpeg manually and add it to PATH."
            )
            self.log_widget.log("FFmpeg not available on PATH or managed install", "ERROR")
            return
        
        self.log_widget.log(f"Found FFmpeg at: {ffmpeg_path}", "INFO")
        
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export Animation", "", "MOV Video (*.mov)"
        )
        
        if not filename:
            return
        
        # Ensure .mov extension
        if not filename.lower().endswith('.mov'):
            filename += '.mov'
        
        fps = self.control_panel.fps_spin.value()
        mov_extra_scale = max(1.0, float(getattr(self.export_settings, 'mov_full_scale_multiplier', 1.0)))
        frame_info = self._render_video_frames(
            fps,
            include_audio=self.export_settings.mov_include_audio,
            use_full_res=self.export_settings.mov_full_resolution,
            extra_scale=mov_extra_scale,
            export_label="MOV",
        )
        if not frame_info:
            return

        temp_dir = frame_info["temp_dir"]
        audio_track_path = frame_info["audio_path"]
        input_pattern = frame_info["input_pattern"]
        output_file = filename.replace('\\', '/')
        mov_codec = self.export_settings.mov_codec

        self.log_widget.log(f"Input pattern: {input_pattern}", "INFO")
        self.log_widget.log(f"Output file: {output_file}", "INFO")
        self.log_widget.log(f"Using codec: {mov_codec}", "INFO")

        def build_ffmpeg_cmd(extra_args, audio_codec='pcm_s16le'):
            cmd = [
                ffmpeg_path,
                '-y',
                '-framerate', str(frame_info["fps"]),
                '-i', input_pattern,
            ]
            if audio_track_path:
                cmd += ['-i', audio_track_path]
            cmd += extra_args
            if audio_track_path:
                cmd += ['-c:a', audio_codec, '-shortest']
            return cmd

        export_success = False

        try:
            # Try ProRes 4444 first - best Adobe compatibility with alpha
            if mov_codec == 'prores_ks' or mov_codec == 'prores':
                self.log_widget.log("Trying ProRes 4444 (best Adobe compatibility)...", "INFO")
                ffmpeg_cmd = build_ffmpeg_cmd([
                    '-c:v', 'prores_ks',
                    '-profile:v', '4444',
                    '-pix_fmt', 'yuva444p10le',
                    '-vendor', 'apl0',
                    output_file
                ])
                
                result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
                
                if result.returncode == 0 and os.path.exists(filename):
                    file_size = os.path.getsize(filename)
                    self.log_widget.log(f"Animation exported (ProRes 4444) to: {filename} ({file_size} bytes)", "SUCCESS")
                    export_success = True
            
            # Try PNG codec - good compatibility, lossless with alpha
            if not export_success and (mov_codec == 'png' or mov_codec == 'prores_ks'):
                self.log_widget.log("Trying PNG codec...", "INFO")
                ffmpeg_cmd = build_ffmpeg_cmd([
                    '-c:v', 'png',
                    '-pix_fmt', 'rgba',
                    output_file
                ])
                
                result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
                
                if result.returncode == 0 and os.path.exists(filename):
                    file_size = os.path.getsize(filename)
                    self.log_widget.log(f"Animation exported (PNG codec) to: {filename} ({file_size} bytes)", "SUCCESS")
                    export_success = True
            
            # Try QuickTime Animation (qtrle) with rgba pixel format
            if not export_success and mov_codec == 'qtrle':
                self.log_widget.log("Trying QuickTime Animation codec...", "INFO")
                ffmpeg_cmd = build_ffmpeg_cmd([
                    '-c:v', 'qtrle',
                    '-pix_fmt', 'argb',
                    output_file
                ])
                
                result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
                
                if result.returncode == 0 and os.path.exists(filename):
                    file_size = os.path.getsize(filename)
                    self.log_widget.log(f"Animation exported (QuickTime Animation) to: {filename} ({file_size} bytes)", "SUCCESS")
                    export_success = True
            
            # Fallback chain if preferred codec failed
            if not export_success:
                self.log_widget.log("Preferred codec failed, trying fallback chain...", "WARNING")
                
                # Try ProRes 4444 as first fallback
                self.log_widget.log("Fallback: Trying ProRes 4444...", "INFO")
                ffmpeg_cmd = build_ffmpeg_cmd([
                    '-c:v', 'prores_ks',
                    '-profile:v', '4444',
                    '-pix_fmt', 'yuva444p10le',
                    '-vendor', 'apl0',
                    output_file
                ])
                
                result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
                
                if result.returncode == 0 and os.path.exists(filename):
                    file_size = os.path.getsize(filename)
                    self.log_widget.log(f"Animation exported (ProRes 4444 fallback) to: {filename} ({file_size} bytes)", "SUCCESS")
                    export_success = True
                else:
                    # Try PNG as second fallback
                    self.log_widget.log("Fallback: Trying PNG codec...", "INFO")
                    ffmpeg_cmd = build_ffmpeg_cmd([
                        '-c:v', 'png',
                        '-pix_fmt', 'rgba',
                        output_file
                    ])
                    
                    result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
                    
                    if result.returncode == 0 and os.path.exists(filename):
                        file_size = os.path.getsize(filename)
                        self.log_widget.log(f"Animation exported (PNG fallback) to: {filename} ({file_size} bytes)", "SUCCESS")
                        export_success = True
                    else:
                        # Final fallback - H.264 without alpha
                        self.log_widget.log("All alpha codecs failed, exporting without transparency...", "WARNING")
                        
                        mp4_file = filename.replace('.mov', '.mp4')
                        ffmpeg_cmd = build_ffmpeg_cmd([
                            '-c:v', 'libx264',
                            '-pix_fmt', 'yuv420p',
                            '-crf', '18',
                            mp4_file
                        ], audio_codec='aac')
                        
                        result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
                        
                        if result.returncode == 0 and os.path.exists(mp4_file):
                            file_size = os.path.getsize(mp4_file)
                            self.log_widget.log(f"Animation exported (no alpha) to: {mp4_file} ({file_size} bytes)", "SUCCESS")
                            export_success = True
                        else:
                            self.log_widget.log(f"All encoding attempts failed. Error: {result.stderr}", "ERROR")
            
        except Exception as e:
            self.log_widget.log(f"Error exporting animation: {e}", "ERROR")
            import traceback
            traceback.print_exc()
        finally:
            self.log_widget.log("Cleaning up temporary files...", "INFO")
            shutil.rmtree(temp_dir, ignore_errors=True)
            self.gl_widget.player.current_time = frame_info["original_time"]
            self.gl_widget.player.playing = frame_info["original_playing"]
            self.gl_widget.update()

    def export_as_mp4(self):
        """Export animation as MP4 video."""
        if not self.gl_widget.player.animation:
            QMessageBox.warning(self, "Error", "No animation loaded")
            return

        ffmpeg_path = self._resolve_ffmpeg_path()
        if not ffmpeg_path:
            QMessageBox.warning(
                self,
                "FFmpeg Required",
                "FFmpeg is required for MP4 export.\n\n"
                "Use Settings > Application > FFmpeg Tools to install it, "
                "or install FFmpeg manually and add it to PATH.",
            )
            self.log_widget.log("FFmpeg not available; MP4 export aborted.", "ERROR")
            return

        filename, _ = QFileDialog.getSaveFileName(
            self, "Export Animation", "", "MP4 Video (*.mp4)"
        )
        if not filename:
            return
        if not filename.lower().endswith('.mp4'):
            filename += '.mp4'

        fps = self.control_panel.fps_spin.value()
        mp4_extra_scale = max(1.0, float(getattr(self.export_settings, 'mp4_full_scale_multiplier', 1.0)))
        frame_info = self._render_video_frames(
            fps,
            include_audio=getattr(self.export_settings, 'mp4_include_audio', True),
            use_full_res=getattr(self.export_settings, 'mp4_full_resolution', False),
            extra_scale=mp4_extra_scale,
            export_label="MP4",
        )
        if not frame_info:
            return

        temp_dir = frame_info["temp_dir"]
        audio_track_path = frame_info["audio_path"]
        input_pattern = frame_info["input_pattern"]
        output_file = filename.replace('\\', '/')
        thread_args = self._ffmpeg_thread_args()

        codec = getattr(self.export_settings, 'mp4_codec', 'libx264') or 'libx264'
        crf = int(getattr(self.export_settings, 'mp4_crf', 18))
        preset = getattr(self.export_settings, 'mp4_preset', 'medium') or 'medium'
        bitrate = int(getattr(self.export_settings, 'mp4_bitrate', 0))
        pix_fmt = getattr(self.export_settings, 'mp4_pixel_format', 'yuv420p') or 'yuv420p'
        faststart = bool(getattr(self.export_settings, 'mp4_faststart', True))

        self.log_widget.log(f"MP4 codec: {codec}, preset={preset}, CRF={crf}", "INFO")
        self.log_widget.log(f"Input pattern: {input_pattern}", "INFO")
        self.log_widget.log(f"Output file: {output_file}", "INFO")

        cmd = [
            ffmpeg_path,
            '-y',
            '-framerate', str(frame_info["fps"]),
            '-i', input_pattern,
        ]
        cmd += thread_args
        if audio_track_path:
            cmd += ['-i', audio_track_path]

        cmd += ['-c:v', codec, '-preset', preset, '-crf', str(crf)]
        if bitrate > 0:
            cmd += ['-b:v', f"{bitrate}k"]
        if pix_fmt:
            cmd += ['-pix_fmt', pix_fmt]
        if codec == 'libx265':
            cmd += ['-tag:v', 'hvc1']
        if faststart:
            cmd += ['-movflags', '+faststart']

        if audio_track_path:
            cmd += ['-c:a', 'aac', '-b:a', '192k', '-shortest']
        else:
            cmd += ['-an']

        cmd.append(output_file)

        export_success = False
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0 and os.path.exists(filename):
                file_size = os.path.getsize(filename)
                self.log_widget.log(f"Animation exported (MP4) to: {filename} ({file_size} bytes)", "SUCCESS")
                export_success = True
            else:
                message = result.stderr.strip() or "Unknown error"
                self.log_widget.log(f"MP4 encoding failed: {message}", "ERROR")
        except Exception as exc:
            self.log_widget.log(f"Error exporting MP4: {exc}", "ERROR")
            import traceback
            traceback.print_exc()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
            self.gl_widget.player.current_time = frame_info["original_time"]
            self.gl_widget.player.playing = frame_info["original_playing"]
            self.gl_widget.update()

        if not export_success:
            QMessageBox.warning(
                self,
                "MP4 Export Failed",
                "FFmpeg was unable to encode the MP4 file. Check the log for details.",
            )

    def export_as_webm(self):
        """Export animation as WEBM video (supports alpha via VP9/AV1)."""
        if not self.gl_widget.player.animation:
            QMessageBox.warning(self, "Error", "No animation loaded")
            return

        ffmpeg_path = self._resolve_ffmpeg_path()
        if not ffmpeg_path:
            QMessageBox.warning(
                self,
                "FFmpeg Required",
                "FFmpeg is required for WEBM export.\n\n"
                "Use Settings > Application > FFmpeg Tools to perform the one-click install, "
                "or install FFmpeg manually and add it to PATH.",
            )
            self.log_widget.log("FFmpeg not available on PATH or managed install", "ERROR")
            return

        filename, _ = QFileDialog.getSaveFileName(
            self, "Export Animation", "", "WEBM Video (*.webm)"
        )
        if not filename:
            return
        if not filename.lower().endswith('.webm'):
            filename += '.webm'

        fps = self.control_panel.fps_spin.value()
        webm_extra_scale = max(1.0, float(getattr(self.export_settings, 'webm_full_scale_multiplier', 1.0)))
        frame_info = self._render_video_frames(
            fps,
            include_audio=self.export_settings.webm_include_audio,
            use_full_res=getattr(self.export_settings, 'webm_full_resolution', False),
            extra_scale=webm_extra_scale,
            export_label="WEBM",
        )
        if not frame_info:
            return

        temp_dir = frame_info["temp_dir"]
        audio_track_path = frame_info["audio_path"]
        input_pattern = frame_info["input_pattern"]
        codec_pref = getattr(self.export_settings, 'webm_codec', 'libvpx-vp9')
        crf = int(getattr(self.export_settings, 'webm_crf', 28))
        speed = int(getattr(self.export_settings, 'webm_speed', 4))
        output_file = filename.replace('\\', '/')
        thread_args = self._ffmpeg_thread_args()

        self.log_widget.log(f"Input pattern: {input_pattern}", "INFO")
        self.log_widget.log(f"Output file: {output_file}", "INFO")
        self.log_widget.log(f"Preferred WEBM codec: {codec_pref}", "INFO")

        def build_video_args(codec_name: str) -> Tuple[List[str], bool]:
            normalized = codec_name.lower()
            supports_alpha = normalized in ('libvpx-vp9', 'libaom-av1')
            pix_fmt = 'yuva420p' if supports_alpha else 'yuv420p'
            args = ['-c:v', normalized, '-pix_fmt', pix_fmt]
            if normalized == 'libvpx-vp9':
                args += ['-b:v', '0', '-crf', str(crf), '-row-mt', '1', '-tile-columns', '2', '-frame-parallel', '1', '-speed', str(speed)]
                if supports_alpha:
                    args += ['-auto-alt-ref', '0']
            elif normalized == 'libaom-av1':
                args += ['-b:v', '0', '-crf', str(crf), '-cpu-used', str(speed), '-row-mt', '1']
            else:
                args += ['-b:v', '0', '-crf', str(crf), '-quality', 'good', '-cpu-used', str(speed)]
            return args, supports_alpha

        def build_ffmpeg_cmd(video_args: List[str], audio_codec: str = 'libopus') -> List[str]:
            cmd = [
                ffmpeg_path,
                '-y',
                '-framerate', str(frame_info["fps"]),
                '-i', input_pattern,
            ]
            cmd += thread_args
            if audio_track_path:
                cmd += ['-i', audio_track_path]
            cmd += video_args
            if audio_track_path:
                cmd += ['-c:a', audio_codec, '-b:a', '160k', '-shortest']
            else:
                cmd += ['-an']
            cmd.append(output_file)
            return cmd

        encode_order: List[str] = [codec_pref]
        if 'libvpx-vp9' not in [c.lower() for c in encode_order]:
            encode_order.append('libvpx-vp9')
        if 'libvpx' not in [c.lower() for c in encode_order]:
            encode_order.append('libvpx')

        export_success = False
        try:
            for codec_name in encode_order:
                video_args, supports_alpha = build_video_args(codec_name)
                if not supports_alpha:
                    self.log_widget.log(
                        f"Codec '{codec_name}' does not support alpha; output will be opaque.",
                        "WARNING",
                    )
                self.log_widget.log(f"Encoding WEBM using {codec_name}...", "INFO")
                ffmpeg_cmd = build_ffmpeg_cmd(video_args)
                result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
                if result.returncode == 0 and os.path.exists(filename):
                    file_size = os.path.getsize(filename)
                    self.log_widget.log(
                        f"Animation exported ({codec_name}) to: {filename} ({file_size} bytes)",
                        "SUCCESS",
                    )
                    export_success = True
                    break
                else:
                    self.log_widget.log(
                        f"{codec_name} encoding failed: {result.stderr.strip()}",
                        "WARNING",
                    )

            if not export_success:
                self.log_widget.log(
                    "WEBM encoding failed, exporting fallback MP4 without alpha.",
                    "WARNING",
                )
                mp4_file = filename.replace('.webm', '.mp4')
                ffmpeg_cmd = [
                    ffmpeg_path,
                    '-y',
                    '-framerate', str(frame_info["fps"]),
                    '-i', input_pattern,
                ]
                ffmpeg_cmd += thread_args
                if audio_track_path:
                    ffmpeg_cmd += ['-i', audio_track_path]
                ffmpeg_cmd += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', mp4_file.replace('\\', '/')]
                if audio_track_path:
                    ffmpeg_cmd += ['-c:a', 'aac', '-b:a', '160k', '-shortest']
                result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
                if result.returncode == 0 and os.path.exists(mp4_file):
                    file_size = os.path.getsize(mp4_file)
                    self.log_widget.log(
                        f"Animation exported (MP4 fallback) to: {mp4_file} ({file_size} bytes)",
                        "SUCCESS",
                    )
                    export_success = True
                else:
                    self.log_widget.log(
                        f"MP4 fallback also failed: {result.stderr.strip()}",
                        "ERROR",
                    )
        finally:
            self.log_widget.log("Cleaning up temporary files...", "INFO")
            shutil.rmtree(temp_dir, ignore_errors=True)
            self.gl_widget.player.current_time = frame_info["original_time"]
            self.gl_widget.player.playing = frame_info["original_playing"]
            self.gl_widget.update()

    def show_settings(self):
        """Show settings dialog"""
        dialog = SettingsDialog(
            self.export_settings,
            self.settings,
            self.shader_registry,
            self.game_path,
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.log_widget.log("Settings saved", "SUCCESS")
            self.gl_widget.set_zoom_to_cursor(self.export_settings.camera_zoom_to_cursor)
            self.gl_widget.set_shader_registry(self.shader_registry)
            self.control_panel.set_barebones_file_mode(self.export_settings.use_barebones_file_browser)
            self._load_audio_preferences_from_storage()
            self._apply_audio_preferences_to_controls()
            self._load_diagnostics_settings()
            self._apply_anchor_logging_preferences()
    
    def export_as_gif(self):
        """Export animation as animated GIF"""
        if not self.gl_widget.player.animation:
            QMessageBox.warning(self, "Error", "No animation loaded")
            return
        
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export GIF", "", "GIF Animation (*.gif)"
        )
        
        if not filename:
            return
        
        # Ensure .gif extension
        if not filename.lower().endswith('.gif'):
            filename += '.gif'
        
        try:
            # Get settings from export_settings
            gif_fps = self.export_settings.gif_fps
            gif_colors = self.export_settings.gif_colors
            gif_scale = self.export_settings.gif_scale / 100.0
            gif_dither = self.export_settings.gif_dither
            gif_optimize = self.export_settings.gif_optimize
            gif_loop = self.export_settings.gif_loop
            
            # Get animation parameters
            real_duration = self._get_export_real_duration()
            total_frames = int(real_duration * gif_fps)
            
            self.log_widget.log(f"GIF Export: {total_frames} frames at {gif_fps} FPS", "INFO")
            self.log_widget.log(f"Settings: {gif_colors} colors, {int(gif_scale*100)}% scale, dither={gif_dither}", "INFO")
            
            if total_frames <= 0:
                QMessageBox.warning(self, "Error", "Animation has no frames to export")
                return
            
            # Calculate output dimensions
            base_width = self.gl_widget.width()
            base_height = self.gl_widget.height()
            output_width = int(base_width * gif_scale)
            output_height = int(base_height * gif_scale)
            
            # Ensure even dimensions
            output_width = output_width if output_width % 2 == 0 else output_width + 1
            output_height = output_height if output_height % 2 == 0 else output_height + 1
            
            self.log_widget.log(f"Output dimensions: {output_width}x{output_height}", "INFO")
            
            # Store original state
            original_time = self.gl_widget.player.current_time
            original_playing = self.gl_widget.player.playing
            self.gl_widget.player.playing = False
            
            # Create progress dialog
            progress = QProgressDialog("Exporting GIF...", "Cancel", 0, total_frames + 1, self)
            progress.setWindowTitle("GIF Export Progress")
            progress.setWindowModality(Qt.WindowModality.WindowModal)
            progress.setMinimumDuration(0)
            progress.setAutoClose(False)
            progress.setAutoReset(False)
            progress.show()
            
            # Render frames
            frames = []
            was_canceled = False
            background_color = self._active_background_color()
            
            for frame_num in range(total_frames):
                if progress.wasCanceled():
                    self.log_widget.log("Export cancelled by user", "WARNING")
                    was_canceled = True
                    break
                
                # Set animation time
                frame_time = self._get_export_frame_time(frame_num, gif_fps)
                self.gl_widget.player.current_time = frame_time
                
                # Render frame at base size
                image = self.render_frame_to_image(
                    base_width,
                    base_height,
                    background_color=background_color,
                )
                
                if image:
                    # Scale if needed
                    if gif_scale != 1.0:
                        image = image.resize((output_width, output_height), Image.Resampling.LANCZOS)
                    
                    # Convert RGBA to palette mode with proper transparency handling
                    # GIF only supports 1-bit transparency (fully transparent or fully opaque)
                    
                    # Get alpha channel
                    alpha = image.split()[3]
                    
                    # Create a mask for transparent pixels (alpha < 128 = transparent)
                    # This threshold can be adjusted - 128 is a good middle ground
                    transparency_threshold = 128
                    
                    # Convert to RGB first (drop alpha temporarily)
                    rgb_image = image.convert('RGB')
                    
                    # Convert to palette mode
                    if gif_dither:
                        palette_image = rgb_image.convert('P', palette=Image.Palette.ADAPTIVE, 
                                                         colors=gif_colors - 1,  # Reserve one color for transparency
                                                         dither=Image.Dither.FLOYDSTEINBERG)
                    else:
                        palette_image = rgb_image.convert('P', palette=Image.Palette.ADAPTIVE, 
                                                         colors=gif_colors - 1,
                                                         dither=Image.Dither.NONE)
                    
                    # Create a new palette image with transparency
                    # We need to set transparent pixels to a specific palette index
                    # First, find or create a transparent color index
                    
                    # Get the palette
                    palette = palette_image.getpalette()
                    
                    # Add a transparent color at the end of the palette (we reserved space)
                    # Use a color that's unlikely to appear in the image (magenta)
                    transparent_index = gif_colors - 1
                    if palette:
                        # Extend palette if needed
                        while len(palette) < transparent_index * 3 + 3:
                            palette.extend([0, 0, 0])
                        palette[transparent_index * 3] = 255      # R
                        palette[transparent_index * 3 + 1] = 0    # G  
                        palette[transparent_index * 3 + 2] = 255  # B (magenta)
                        palette_image.putpalette(palette)
                    
                    # Now apply transparency mask
                    # Convert palette image to array for manipulation
                    palette_array = np.array(palette_image)
                    alpha_array = np.array(alpha)
                    
                    # Set transparent pixels to the transparent index
                    palette_array[alpha_array < transparency_threshold] = transparent_index
                    
                    # Create new palette image from array
                    final_image = Image.fromarray(palette_array, mode='P')
                    final_image.putpalette(palette)
                    
                    # Store the transparent index for this frame
                    final_image.info['transparency'] = transparent_index
                    
                    frames.append(final_image)
                else:
                    self.log_widget.log(f"Failed to render frame {frame_num}", "WARNING")
                
                progress.setValue(frame_num + 1)
                progress.setLabelText(f"Rendering frame {frame_num + 1} of {total_frames}...")
                
                # Process events
                from PyQt6.QtWidgets import QApplication
                QApplication.processEvents()
            
            if was_canceled or len(frames) == 0:
                self.log_widget.log(f"Export aborted. Frames rendered: {len(frames)}", "WARNING")
                self.gl_widget.player.current_time = original_time
                self.gl_widget.player.playing = original_playing
                progress.close()
                return
            
            progress.setLabelText("Encoding GIF...")
            progress.setValue(total_frames)
            from PyQt6.QtWidgets import QApplication
            QApplication.processEvents()
            
            # Calculate frame duration in milliseconds
            frame_duration = int(1000 / gif_fps)
            
            # Save as GIF
            self.log_widget.log(f"Saving GIF with {len(frames)} frames...", "INFO")
            
            # Get the transparent index from the first frame
            transparent_index = gif_colors - 1
            
            # Use the first frame as base, append the rest
            frames[0].save(
                filename,
                save_all=True,
                append_images=frames[1:] if len(frames) > 1 else [],
                duration=frame_duration,
                loop=gif_loop,
                optimize=gif_optimize,
                transparency=transparent_index,
                disposal=2  # Restore to background between frames
            )
            
            progress.close()
            
            # Get file size
            file_size = os.path.getsize(filename)
            if file_size > 1024 * 1024:
                size_str = f"{file_size / (1024*1024):.2f} MB"
            else:
                size_str = f"{file_size / 1024:.1f} KB"
            
            self.log_widget.log(f"GIF exported to: {filename} ({size_str})", "SUCCESS")
            
            # Restore original state
            self.gl_widget.player.current_time = original_time
            self.gl_widget.player.playing = original_playing
            self.gl_widget.update()
            
            QMessageBox.information(
                self, "Export Complete",
                f"GIF exported successfully!\n\n"
                f"File: {filename}\n"
                f"Size: {size_str}\n"
                f"Frames: {len(frames)}\n"
                f"Dimensions: {output_width}x{output_height}"
            )
            
        except Exception as e:
            self.log_widget.log(f"Error exporting GIF: {e}", "ERROR")
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self, "Export Error", f"Failed to export GIF: {e}")
    
    def show_credits(self):
        """Show credits and acknowledgments dialog"""
        credits_dialog = QDialog(self)
        credits_dialog.setWindowTitle("Credits & Acknowledgments")
        credits_dialog.setMinimumWidth(450)
        credits_dialog.setMinimumHeight(400)
        
        layout = QVBoxLayout(credits_dialog)
        
        # Title with glow effect
        title_label = QLabel("MSM Animation Viewer")
        title_label.setStyleSheet("font-size: 18pt; font-weight: bold; color: white;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Add white glow effect
        glow_effect = QGraphicsDropShadowEffect()
        glow_effect.setBlurRadius(20)
        glow_effect.setColor(QColor(255, 255, 255))
        glow_effect.setOffset(0, 0)
        title_label.setGraphicsEffect(glow_effect)
        
        layout.addWidget(title_label)
        
        # Created by
        created_label = QLabel("Created by <b>LennyFaze</b> (MSM Green Screens)")
        created_label.setStyleSheet("font-size: 10pt; color: #aaa;")
        created_label.setTextFormat(Qt.TextFormat.RichText)
        created_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(created_label)
        
        # Subtitle
        subtitle_label = QLabel(f"Credits & Acknowledgments — Build {self.build_version}")
        subtitle_label.setStyleSheet("font-size: 12pt; color: #666;")
        subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle_label)
        
        layout.addSpacing(20)
        
        # Credits content
        credits_text = """
<div style="font-size: 10pt;">

<p style="font-weight: bold; color: #4a90d9; font-size: 11pt;">Special Thanks</p>

<p><b>iestyn129</b><br/>
<i>For the bin2json parsing script that made this project possible,<br/>
alpha testing, and valuable feedback</i></p>

<p><b>wubbox64</b><br/>
<i>Alpha testing and valuable feedback</i></p>

<hr style="border: 1px solid #ddd; margin: 15px 0;"/>

<p><b>The MSM Community</b><br/>
<i>For their continued support and enthusiasm for this project!</i></p>

<hr style="border: 1px solid #ddd; margin: 15px 0;"/>

<p style="font-weight: bold; color: #4a90d9; font-size: 11pt;">Legal</p>

<p style="color: #888; font-size: 9pt;">
<b>My Singing Monsters</b> is a registered trademark of<br/>
<b>Big Blue Bubble Inc.</b><br/><br/>
This tool is a fan-made project and is not affiliated with,<br/>
endorsed by, or connected to Big Blue Bubble Inc.<br/><br/>
All game assets and content are owned by Big Blue Bubble Inc.
</p>

</div>
"""
        
        credits_label = QLabel(credits_text)
        credits_label.setWordWrap(True)
        credits_label.setTextFormat(Qt.TextFormat.RichText)
        credits_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(credits_label)
        
        layout.addStretch()
        
        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(credits_dialog.accept)
        close_btn.setStyleSheet("padding: 8px 30px;")
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        
        credits_dialog.exec()
    
    def load_settings(self):
        """Load saved settings"""
        if self.game_path:
            self.path_label.setText(f"Game Path: {self.game_path}")
            self.build_audio_library()
            self.refresh_file_list()
    
    def on_layer_selection_changed(self, layer_ids: List[int], last_layer_id: int, added: bool):
        """Handle layer selection toggles from the layer panel."""
        self.selected_layer_ids = set(layer_ids)
        if not self.selected_layer_ids:
            self.primary_selected_layer_id = None
        else:
            if added and last_layer_id in self.selected_layer_ids:
                self.primary_selected_layer_id = last_layer_id
            elif self.primary_selected_layer_id not in self.selected_layer_ids:
                self.primary_selected_layer_id = next(iter(self.selected_layer_ids))
        self.layer_panel.set_selection_state(self.selected_layer_ids)
        self.apply_selection_state()
        self._refresh_timeline_keyframes()
        self._update_nudge_controls_state()
    
    def on_selection_lock_toggled(self, locked: bool):
        """Handle lock/unlock requests from the layer panel."""
        if locked and not self.selected_layer_ids:
            locked = False
        self.selection_lock_enabled = locked
        self.apply_selection_state()
    
    def on_layer_selection_cleared(self):
        """Handle deselect-all events from the layer panel."""
        if not self.selected_layer_ids and not self.selection_lock_enabled:
            return
        self.selected_layer_ids.clear()
        self.primary_selected_layer_id = None
        self.selection_lock_enabled = False
        self.apply_selection_state()
        self._refresh_timeline_keyframes()
        self._update_nudge_controls_state()
    
    def apply_selection_state(self):
        """Push current selection info to the GL widget."""
        self.gl_widget.set_selection_state(
            self.selected_layer_ids,
            self.primary_selected_layer_id,
            self.selection_lock_enabled
        )

    def on_layer_color_changed(self, r: int, g: int, b: int, a: int):
        """Apply a tint override to all selected layers."""
        animation = self.gl_widget.player.animation
        if not animation or not self.selected_layer_ids:
            return

        def _clamp(value: int) -> int:
            return max(0, min(255, int(value)))

        rgba = tuple(_clamp(v) for v in (r, g, b, a))
        tint = tuple(channel / 255.0 for channel in rgba)
        reset = rgba == (255, 255, 255, 255)
        updated = 0

        for layer in animation.layers:
            if layer.layer_id not in self.selected_layer_ids:
                continue
            layer.color_tint = None if reset else tint
            updated += 1
            if hasattr(self, "diagnostics") and self.diagnostics:
                if reset:
                    self.diagnostics.log_color("Cleared tint override", layer_id=layer.layer_id)
                else:
                    hex_value = f"#{rgba[0]:02X}{rgba[1]:02X}{rgba[2]:02X}{rgba[3]:02X}"
                    self.diagnostics.log_color(
                        f"Applied tint {hex_value}", layer_id=layer.layer_id
                    )

        if updated:
            self.gl_widget.update()
            self.layer_panel.refresh_color_editor()
            if reset:
                self.log_widget.log(f"Cleared tint on {updated} layer(s)", "INFO")
            else:
                self.log_widget.log(
                    f"Set tint for {updated} layer(s) to "
                    f"{rgba[0]:02d},{rgba[1]:02d},{rgba[2]:02d},{rgba[3]:02d}",
                    "INFO"
                )

    def on_layer_color_reset(self):
        """Remove tint overrides for selected layers."""
        self.on_layer_color_changed(255, 255, 255, 255)

    def save_layer_offsets(self):
        """Save current layer offsets/rotations to a text file."""
        if not self.gl_widget.player.animation:
            QMessageBox.warning(self, "No Animation", "Load an animation before saving offsets.")
            return

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save Layer Offsets",
            "",
            "Layer Preset (*.txt);;All Files (*.*)"
        )
        if not filename:
            return
        if not filename.lower().endswith(".txt"):
            filename += ".txt"

        animation = self.gl_widget.player.animation
        data = {
            "animation": animation.name,
            "saved_at": datetime.now().isoformat(),
            "layers": []
        }
        for layer in animation.layers:
            offset_x, offset_y = self.gl_widget.layer_offsets.get(layer.layer_id, (0.0, 0.0))
            rotation = self.gl_widget.layer_rotations.get(layer.layer_id, 0.0)
            scale_x, scale_y = self.gl_widget.layer_scale_offsets.get(layer.layer_id, (1.0, 1.0))
            data["layers"].append({
                "id": layer.layer_id,
                "name": layer.name,
                "offset_x": offset_x,
                "offset_y": offset_y,
                "rotation": rotation,
                "scale_x": scale_x,
                "scale_y": scale_y
            })
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self.log_widget.log(f"Saved {len(data['layers'])} layer offsets to {filename}", "SUCCESS")
        except Exception as exc:
            self.log_widget.log(f"Failed to save offsets: {exc}", "ERROR")
            QMessageBox.warning(self, "Save Error", f"Could not save offsets:\n{exc}")

    def load_layer_offsets(self):
        """Load previously saved layer offsets and apply them to the animation."""
        if not self.gl_widget.player.animation:
            QMessageBox.warning(self, "No Animation", "Load an animation before applying offsets.")
            return

        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Load Layer Offsets",
            "",
            "Layer Preset (*.txt);;All Files (*.*)"
        )
        if not filename:
            return

        try:
            with open(filename, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            self.log_widget.log(f"Failed to read offsets: {exc}", "ERROR")
            QMessageBox.warning(self, "Load Error", f"Could not read offsets:\n{exc}")
            return

        entries = data.get("layers")
        if not isinstance(entries, list):
            QMessageBox.warning(self, "Invalid File", "Selected file does not contain layer offset data.")
            return

        current_animation = self.gl_widget.player.animation
        if data.get("animation") and data["animation"] != current_animation.name:
            self.log_widget.log("Preset animation mismatch; applying anyway", "WARNING")

        layer_map = {layer.layer_id: layer for layer in current_animation.layers}

        self.gl_widget.layer_offsets.clear()
        self.gl_widget.layer_rotations.clear()

        applied = 0
        for entry in entries:
            layer_id = entry.get("id")
            if layer_id not in layer_map:
                continue
            offset_x = float(entry.get("offset_x", 0.0))
            offset_y = float(entry.get("offset_y", 0.0))
            rotation = float(entry.get("rotation", 0.0))
            scale_x = float(entry.get("scale_x", 1.0))
            scale_y = float(entry.get("scale_y", 1.0))

            if abs(offset_x) > 1e-6 or abs(offset_y) > 1e-6:
                self.gl_widget.layer_offsets[layer_id] = (offset_x, offset_y)
            if abs(rotation) > 1e-6:
                self.gl_widget.layer_rotations[layer_id] = rotation
            if abs(scale_x - 1.0) > 1e-6 or abs(scale_y - 1.0) > 1e-6:
                self.gl_widget.layer_scale_offsets[layer_id] = (scale_x, scale_y)
            applied += 1

        self.update_offset_display()
        self.gl_widget.update()
        self.log_widget.log(f"Applied offsets to {applied} layers from {os.path.basename(filename)}", "SUCCESS")
    
    def reset_sprite_offsets(self):
        """Reset all sprite offsets to default"""
        self.gl_widget.reset_layer_offsets()
        self.update_offset_display()
        self.log_widget.log("Reset all sprite offsets to default", "SUCCESS")
    
    def update_offset_display(self):
        """Update the offset display with current values"""
        self.control_panel.update_offset_display(
            self.gl_widget.layer_offsets,
            self.gl_widget.get_layer_by_id,
            self.gl_widget.layer_rotations,
            self.gl_widget.layer_scale_offsets
        )
        
        # Start a timer to update offsets periodically
        if not hasattr(self, 'offset_update_timer'):
            self.offset_update_timer = QTimer(self)
            self.offset_update_timer.timeout.connect(self.update_offset_display)
            self.offset_update_timer.start(100)  # Update every 100ms

    # ------------------------------------------------------------------ #
    # Pixel Nudging Handlers
    # ------------------------------------------------------------------ #
    def on_nudge_x(self, delta: float):
        """Nudge selected layers by delta pixels in X direction."""
        targets = self._get_nudge_targets()
        if not targets:
            self.log_widget.log("No layers selected for nudging", "WARNING")
            return
        for layer_id in targets:
            old_x, old_y = self.gl_widget.layer_offsets.get(layer_id, (0.0, 0.0))
            self.gl_widget.layer_offsets[layer_id] = (old_x + delta, old_y)
        self.gl_widget.update()
        self.update_offset_display()

    def on_nudge_y(self, delta: float):
        """Nudge selected layers by delta pixels in Y direction."""
        targets = self._get_nudge_targets()
        if not targets:
            self.log_widget.log("No layers selected for nudging", "WARNING")
            return
        for layer_id in targets:
            old_x, old_y = self.gl_widget.layer_offsets.get(layer_id, (0.0, 0.0))
            self.gl_widget.layer_offsets[layer_id] = (old_x, old_y + delta)
        self.gl_widget.update()
        self.update_offset_display()

    def on_nudge_rotation(self, delta: float):
        """Nudge selected layers by delta degrees in rotation."""
        targets = self._get_nudge_targets()
        if not targets:
            self.log_widget.log("No layers selected for nudging", "WARNING")
            return
        for layer_id in targets:
            old_rot = self.gl_widget.layer_rotations.get(layer_id, 0.0)
            self.gl_widget.layer_rotations[layer_id] = old_rot + delta
        self.gl_widget.update()
        self.update_offset_display()

    def on_nudge_scale_x(self, delta: float):
        """Nudge selected layers by delta in X scale."""
        targets = self._get_nudge_targets()
        if not targets:
            self.log_widget.log("No layers selected for nudging", "WARNING")
            return
        for layer_id in targets:
            old_sx, old_sy = self.gl_widget.layer_scale_offsets.get(layer_id, (1.0, 1.0))
            new_sx = max(0.01, old_sx + delta)  # Prevent negative/zero scale
            self.gl_widget.layer_scale_offsets[layer_id] = (new_sx, old_sy)
        self.gl_widget.update()
        self.update_offset_display()

    def on_nudge_scale_y(self, delta: float):
        """Nudge selected layers by delta in Y scale."""
        targets = self._get_nudge_targets()
        if not targets:
            self.log_widget.log("No layers selected for nudging", "WARNING")
            return
        for layer_id in targets:
            old_sx, old_sy = self.gl_widget.layer_scale_offsets.get(layer_id, (1.0, 1.0))
            new_sy = max(0.01, old_sy + delta)  # Prevent negative/zero scale
            self.gl_widget.layer_scale_offsets[layer_id] = (old_sx, new_sy)
        self.gl_widget.update()
        self.update_offset_display()

    def _get_nudge_targets(self) -> List[int]:
        """Return the list of layer IDs to apply nudging to."""
        if self.selected_layer_ids:
            return list(self.selected_layer_ids)
        if self.primary_selected_layer_id is not None:
            return [self.primary_selected_layer_id]
        return []

    def _update_nudge_controls_state(self):
        """Enable/disable nudge controls based on current layer selection."""
        has_selection = bool(self.selected_layer_ids) or self.primary_selected_layer_id is not None
        self.control_panel.set_nudge_controls_enabled(has_selection)

    def closeEvent(self, event):
        """Handle window close"""
        self.settings.setValue('game_path', self.game_path)

        # Stop offset update timer
        if hasattr(self, 'offset_update_timer'):
            self.offset_update_timer.stop()

        event.accept()

    def _compute_png_export_params(self):
        """Return common settings for PNG exports."""
        if not self.gl_widget.player.animation:
            return None

        use_full_res = getattr(self.export_settings, 'png_full_resolution', False)
        png_extra_scale = max(1.0, float(getattr(self.export_settings, 'png_full_scale_multiplier', 1.0)))
        full_res_scale = (self._get_full_resolution_scale() * png_extra_scale) if use_full_res else 1.0

        camera_override = None
        render_scale_override = None
        apply_centering = True

        bounds = None
        if use_full_res:
            fps = max(1, self.control_panel.fps_spin.value())
            bounds = self._compute_animation_bounds(fps)
        if bounds is None:
            bounds = self._compute_frame_bounds(self.gl_widget.player.current_time)

        if use_full_res and bounds:
                min_x, min_y, max_x, max_y = bounds
                padding = 8.0
                min_x -= padding / 2.0
                min_y -= padding / 2.0
                max_x += padding / 2.0
                max_y += padding / 2.0
                width_units = max(1e-3, max_x - min_x)
                height_units = max(1e-3, max_y - min_y)
                export_width = max(1, int(math.ceil(width_units * full_res_scale)))
                export_height = max(1, int(math.ceil(height_units * full_res_scale)))
                render_scale_override = full_res_scale
                center_x = min_x + width_units / 2.0
                center_y = min_y + height_units / 2.0
                camera_override = (
                    export_width * 0.5 - render_scale_override * center_x,
                    export_height * 0.5 - render_scale_override * center_y
                )
                apply_centering = False
                return export_width, export_height, camera_override, render_scale_override, apply_centering

        # Fallback to viewport size
        export_width = self.gl_widget.width()
        export_height = self.gl_widget.height()
        render_scale_override = None
        camera_override = None
        apply_centering = True
        return export_width, export_height, camera_override, render_scale_override, apply_centering

    def _ensure_pytoshop_available(self):
        """Return the pytoshop module, installing it automatically if needed."""
        if self._pytoshop is not None:
            return self._pytoshop
        try:
            module = importlib.import_module("pytoshop")
            self._pytoshop = module
            return module
        except ImportError:
            self.log_widget.log(
                "pytoshop is not installed for this interpreter. Attempting automatic install...",
                "WARNING"
            )
            installer = PytoshopInstaller(
                log_fn=lambda msg, level="INFO": self.log_widget.log(msg, level)
            )
            if not installer.install_latest():
                if not self._install_python_package("pytoshop>=1.2.1"):
                    self._show_pytoshop_install_help()
                    return None
            try:
                importlib.invalidate_caches()
                module = importlib.import_module("pytoshop")
                self._pytoshop = module
                return module
            except ImportError:
                self._show_pytoshop_install_help()
                return None
            except Exception as exc:
                self.log_widget.log(f"pytoshop import failed after install: {exc}", "ERROR")
                self._show_pytoshop_install_help(extra_detail=str(exc))
                return None

    def _install_python_package(self, package_spec: str) -> bool:
        """Install a package using pip for the current interpreter."""
        python_exe = sys.executable or "python"
        try:
            subprocess.check_call([python_exe, "-m", "pip", "install", package_spec])
            self.log_widget.log(f"Installed dependency: {package_spec}", "SUCCESS")
            return True
        except Exception as exc:
            self.log_widget.log(f"Failed to install {package_spec}: {exc}", "ERROR")
            return False

    def _show_pytoshop_install_help(self, extra_detail: str = ""):
        """Display guidance on installing pytoshop manually."""
        python_exe = sys.executable or "python"
        message = (
            "PSD export requires the 'pytoshop' package but it could not be imported.\n\n"
            f"Install it for this interpreter with:\n  \"{python_exe}\" -m pip install pytoshop\n"
        )
        if extra_detail:
            message += f"\nDetails: {extra_detail}"
        QMessageBox.warning(self, "Missing pytoshop", message)
        self.log_widget.log("PSD export aborted because pytoshop is unavailable.", "ERROR")

    def _ensure_packbits_available(self) -> bool:
        """Ensure the standalone 'packbits' module used by pytoshop is importable."""
        packbits_module = None
        try:
            import packbits as packbits_import  # type: ignore
            packbits_module = packbits_import
        except ImportError:
            self.log_widget.log(
                "'packbits' dependency missing; installing now...",
                "WARNING"
            )
            installer = PythonPackageInstaller(
                "packbits",
                "packbits>=0.1.0",
                log_fn=lambda msg, level="INFO": self.log_widget.log(msg, level),
            )
            if installer.install_latest():
                try:
                    importlib.invalidate_caches()
                    import packbits as packbits_import  # type: ignore
                    packbits_module = packbits_import
                    self.log_widget.log("packbits installed successfully.", "SUCCESS")
                except ImportError as exc:
                    self.log_widget.log(f"packbits import failed after install: {exc}", "WARNING")
            if packbits_module is None:
                self.log_widget.log(
                    "Falling back to built-in packbits implementation.",
                    "WARNING"
                )
                packbits_module = self._create_packbits_fallback()

        if packbits_module is None:
            QMessageBox.warning(
                self,
                "Missing packbits",
                "Unable to load or emulate the 'packbits' module required for PSD export."
            )
            self.log_widget.log("packbits unavailable; PSD export aborted.", "ERROR")
            return False

        sys.modules['packbits'] = packbits_module  # type: ignore
        try:
            codecs_module = importlib.import_module("pytoshop.codecs")
        except Exception as exc:
            self.log_widget.log(f"Failed to import pytoshop.codecs: {exc}", "ERROR")
            return False
        setattr(codecs_module, "packbits", packbits_module)  # type: ignore
        try:
            importlib.reload(codecs_module)
        except Exception as exc:
            self.log_widget.log(f"Failed to reload pytoshop codecs after packbits install: {exc}", "WARNING")
        return True

    @staticmethod
    def _create_packbits_fallback():
        """Return a pure-python fallback for packbits encode/decode."""
        module = types.ModuleType("packbits_fallback")

        def decode(data):
            buffer = bytearray(data)
            result = bytearray()
            pos = 0
            length = len(buffer)
            while pos < length:
                header = buffer[pos]
                if header > 127:
                    header -= 256
                pos += 1
                if 0 <= header <= 127:
                    count = header + 1
                    result.extend(buffer[pos:pos + count])
                    pos += count
                elif header == -128:
                    continue
                else:
                    count = 1 - header
                    if pos < length:
                        result.extend([buffer[pos]] * count)
                        pos += 1
            return bytes(result)

        def encode(data):
            data = bytes(data)
            length = len(data)
            if length <= 1:
                return b'\x00' + data if length == 1 else data
            idx = 0
            result = bytearray()
            raw_buf = bytearray()

            def flush_raw():
                if not raw_buf:
                    return
                result.append(len(raw_buf) - 1)
                result.extend(raw_buf)
                raw_buf[:] = b''

            while idx < length:
                run_start = idx
                run_byte = data[idx]
                idx += 1
                while idx < length and data[idx] == run_byte and idx - run_start < 128:
                    idx += 1
                run_length = idx - run_start
                if run_length >= 3:
                    flush_raw()
                    result.append(257 - run_length)
                    result.append(run_byte)
                else:
                    raw_buf.extend(data[run_start:run_start + run_length])
                    while len(raw_buf) >= 128:
                        result.append(127)
                        result.extend(raw_buf[:128])
                        raw_buf = raw_buf[128:]
            flush_raw()
            return bytes(result)

        module.encode = encode
        module.decode = decode
        return module
    def _apply_sheet_aliases_to_base_atlases(
        self,
        base_atlases: List[TextureAtlas],
        aliases: Dict[str, List[str]]
    ) -> List[TextureAtlas]:
        """Reorder base atlases so alias-targeted sheets remain available as a fallback."""
        if not aliases:
            return list(base_atlases)
        prioritized: List[TextureAtlas] = []
        remaining: List[TextureAtlas] = []
        alias_keys = set(aliases.keys())
        for atlas in base_atlases:
            sheet_name = getattr(atlas, "source_name", None) or atlas.image_path
            keys = self._canonical_sheet_keys(sheet_name)
            if keys and alias_keys.intersection(keys):
                prioritized.append(atlas)
            else:
                remaining.append(atlas)
        return prioritized + remaining
    def _configure_costume_shaders(self, entry: Optional[CostumeEntry], costume_data: Optional[Dict[str, Any]]):
        """Automatic shader texture overrides derived from costume metadata."""
        if not entry or not costume_data:
            self.shader_registry.set_runtime_overrides({})
            return
        shader_defs = costume_data.get('apply_shader') or []
        if not shader_defs:
            self.shader_registry.set_runtime_overrides({})
            return

        layer_sheet_lookup, fallback_sheets = self._build_shader_sheet_lookup(costume_data)
        overrides: Dict[str, Dict[str, Any]] = {}
        for shader in shader_defs:
            node = (shader or {}).get('node')
            resource = (shader or {}).get('resource')
            if not resource:
                continue
            behavior = self.shader_registry.get_behavior(resource)
            texture_path: Optional[str] = None
            if not behavior or behavior.requires_texture:
                texture_path = self._resolve_shader_texture_path(
                    entry,
                    behavior,
                    node,
                    layer_sheet_lookup,
                    fallback_sheets,
                )
            self.shader_registry.register_costume_shader(
                resource,
                costume_key=entry.key,
                node=node,
                texture_path=texture_path,
            )
            if not behavior:
                self.log_widget.log(
                    f"No shader behavior metadata for '{resource}'. "
                    "Add an entry to costume_shader_behaviors.json to animate it.",
                    "INFO",
                )
                continue
            if behavior.requires_texture and not texture_path:
                self.log_widget.log(
                    f"Unable to resolve texture for shader '{resource}' (costume {entry.display_name}).",
                    "WARNING"
                )
                continue
            metadata = {"behavior": behavior.name}
            if texture_path:
                metadata["sequence_texture"] = texture_path
            overrides[resource.lower()] = {
                "metadata": metadata
            }
        self.shader_registry.set_runtime_overrides(overrides)

    def _build_shader_sheet_lookup(
        self, costume_data: Dict[str, Any]
    ) -> Tuple[Dict[str, List[str]], List[str]]:
        """Return layer->sheet mappings and fallback sheet bases for shader lookups."""
        lookup: Dict[str, List[str]] = {}
        fallbacks: List[str] = []

        def _add_layer_mapping(layer_name: Optional[str], sheet_name: Optional[str]):
            base = self._sheet_base_name(sheet_name)
            if not layer_name or not base:
                return
            for variant in self._layer_name_variants(layer_name):
                if not variant:
                    continue
                key = variant.lower()
                slots = lookup.setdefault(key, [])
                if base not in slots:
                    slots.append(base)

        for remap in costume_data.get('remaps', []):
            _add_layer_mapping(remap.get('display_name'), remap.get('sheet'))

        sheet_swaps = costume_data.get('sheet_remaps') or costume_data.get('swaps') or []
        for swap in sheet_swaps:
            base = self._sheet_base_name(swap.get('to'))
            if base:
                self._append_unique(fallbacks, base)

        for source in costume_data.get('sources', []):
            base = self._sheet_base_name(source.get('src'))
            if base:
                self._append_unique(fallbacks, base)

        for alias_targets in self.costume_sheet_aliases.values():
            for alias in alias_targets:
                base = self._sheet_base_name(alias)
                if base:
                    self._append_unique(fallbacks, base)

        return lookup, fallbacks

    def _match_sheet_candidates_for_node(
        self,
        node_name: Optional[str],
        sheet_lookup: Dict[str, List[str]]
    ) -> List[str]:
        if not node_name:
            return []
        matches: List[str] = []
        for variant in self._layer_name_variants(node_name):
            if not variant:
                continue
            key = variant.lower()
            options = sheet_lookup.get(key)
            if not options:
                continue
            for candidate in options:
                if candidate not in matches:
                    matches.append(candidate)
        return matches

    @staticmethod
    def _sheet_base_name(sheet: Optional[str]) -> Optional[str]:
        if not sheet:
            return None
        stem = Path(sheet).stem
        lowered = stem.lower()
        if lowered.endswith("_sheet"):
            stem = stem[: -len("_sheet")]
        return stem or None

    @staticmethod
    def _append_unique(collection: List[str], value: Optional[str]) -> None:
        if not value:
            return
        if value not in collection:
            collection.append(value)

    def _locate_costume_texture(self, base_name: Optional[str]) -> Optional[str]:
        if not base_name or not self.game_path:
            return None
        costume_dir = Path(self.game_path) / "data" / "gfx" / "costumes"
        if not costume_dir.exists():
            return None
        for ext in (".avif", ".png", ".dds", ".jpg", ".jpeg", ".tga", ".bmp"):
            candidate = costume_dir / f"{base_name}{ext}"
            if candidate.exists():
                return str(candidate)
        return None

    def _resolve_shader_texture_path(
        self,
        entry: CostumeEntry,
        behavior,
        node_name: Optional[str],
        sheet_lookup: Dict[str, List[str]],
        fallback_sheets: List[str],
    ) -> Optional[str]:
        if not self.game_path:
            return None

        candidates: List[str] = []
        candidates.extend(self._match_sheet_candidates_for_node(node_name, sheet_lookup))
        candidates.extend(fallback_sheets)
        prefix = self._costume_texture_prefix(entry.key)
        if prefix:
            candidates.append(prefix)

        ordered_bases: List[str] = []
        for candidate in candidates:
            if candidate and candidate not in ordered_bases:
                ordered_bases.append(candidate)

        suffixes: List[str] = []
        if behavior and behavior.texture_suffix:
            suffixes.append(behavior.texture_suffix)
        suffixes.extend(["_sequence", ""])
        dedup_suffixes: List[str] = []
        for suffix in suffixes:
            if suffix not in dedup_suffixes:
                dedup_suffixes.append(suffix)

        for base in ordered_bases:
            for suffix in dedup_suffixes:
                target = base
                if suffix and not base.lower().endswith(suffix.lower()):
                    target = f"{base}{suffix}"
                path = self._locate_costume_texture(target)
                if path:
                    return path
        return None

    @staticmethod
    def _costume_texture_prefix(entry_key: str) -> Optional[str]:
        if not entry_key.startswith("costume_"):
            return None
        parts = entry_key.split("_")[1:]
        if len(parts) < 2:
            return None
        index = parts[-1]
        token = "_".join(parts[:-1]).upper()
        return f"monster_{token}_costume_{index}"
