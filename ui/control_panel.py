"""
Control Panel
Main control panel with file selection, animation controls, and settings
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QPushButton, QSpinBox, QDoubleSpinBox,
    QSlider, QCheckBox, QGroupBox, QScrollArea, QLineEdit,
    QListView, QSizePolicy, QColorDialog
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from typing import Any, List, Tuple


class FullscreenSafeComboBox(QComboBox):
    """ComboBox whose popup stays visible when the main window is fullscreen."""

    def __init__(self, parent=None):
        super().__init__(parent)
        view = QListView(self)
        view.setUniformItemSizes(True)
        self.setView(view)

    def showPopup(self):
        view = self.view()
        popup = view.window()
        desired_flags = popup.windowFlags() | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        if popup.windowFlags() != desired_flags:
            popup.setWindowFlags(desired_flags)
            popup.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        super().showPopup()
        popup.raise_()
        popup.activateWindow()
        view.setFocus(Qt.FocusReason.PopupFocusReason)


class ControlPanel(QWidget):
    """Control panel with all main controls"""
    
    # Signals
    bin_selected = pyqtSignal(int)
    convert_bin_clicked = pyqtSignal()
    refresh_files_clicked = pyqtSignal()
    animation_selected = pyqtSignal(int)
    costume_selected = pyqtSignal(int)
    costume_convert_clicked = pyqtSignal()
    scale_changed = pyqtSignal(float)
    fps_changed = pyqtSignal(int)
    position_scale_changed = pyqtSignal(float)
    position_scale_slider_changed = pyqtSignal(int)
    base_world_scale_changed = pyqtSignal(float)
    base_world_scale_slider_changed = pyqtSignal(int)
    reset_camera_clicked = pyqtSignal()
    fit_to_view_clicked = pyqtSignal()
    show_bones_toggled = pyqtSignal(bool)
    tweening_toggled = pyqtSignal(bool)
    reset_offsets_clicked = pyqtSignal()
    export_frame_clicked = pyqtSignal()
    export_frames_sequence_clicked = pyqtSignal()
    export_psd_clicked = pyqtSignal()
    export_mov_clicked = pyqtSignal()
    export_mp4_clicked = pyqtSignal()
    export_webm_clicked = pyqtSignal()
    export_gif_clicked = pyqtSignal()
    credits_clicked = pyqtSignal()
    file_search_changed = pyqtSignal(str)
    monster_browser_requested = pyqtSignal()
    translation_sensitivity_changed = pyqtSignal(float)
    rotation_sensitivity_changed = pyqtSignal(float)
    rotation_overlay_size_changed = pyqtSignal(float)
    rotation_gizmo_toggled = pyqtSignal(bool)
    anchor_overlay_toggled = pyqtSignal(bool)
    parent_overlay_toggled = pyqtSignal(bool)
    anchor_drag_precision_changed = pyqtSignal(float)
    anchor_bias_x_changed = pyqtSignal(float)
    anchor_bias_y_changed = pyqtSignal(float)
    local_position_multiplier_changed = pyqtSignal(float)
    parent_mix_changed = pyqtSignal(float)
    rotation_bias_changed = pyqtSignal(float)
    scale_bias_x_changed = pyqtSignal(float)
    scale_bias_y_changed = pyqtSignal(float)
    world_offset_x_changed = pyqtSignal(float)
    world_offset_y_changed = pyqtSignal(float)
    trim_shift_multiplier_changed = pyqtSignal(float)
    antialias_toggled = pyqtSignal(bool)
    audio_enabled_changed = pyqtSignal(bool)
    audio_volume_changed = pyqtSignal(int)
    save_offsets_clicked = pyqtSignal()
    load_offsets_clicked = pyqtSignal()
    nudge_x_changed = pyqtSignal(float)
    nudge_y_changed = pyqtSignal(float)
    nudge_rotation_changed = pyqtSignal(float)
    nudge_scale_x_changed = pyqtSignal(float)
    nudge_scale_y_changed = pyqtSignal(float)
    scale_gizmo_toggled = pyqtSignal(bool)
    scale_gizmo_mode_changed = pyqtSignal(str)
    bpm_value_changed = pyqtSignal(float)
    sync_audio_to_bpm_toggled = pyqtSignal(bool)
    pitch_shift_toggled = pyqtSignal(bool)
    bpm_reset_requested = pyqtSignal()
    base_bpm_lock_requested = pyqtSignal()
    diagnostics_enabled_changed = pyqtSignal(bool)
    diagnostics_refresh_requested = pyqtSignal()
    diagnostics_export_requested = pyqtSignal()
    pose_record_clicked = pyqtSignal()
    pose_mode_changed = pyqtSignal(str)
    pose_reset_clicked = pyqtSignal()
    keyframe_undo_clicked = pyqtSignal()
    keyframe_redo_clicked = pyqtSignal()
    keyframe_delete_others_clicked = pyqtSignal()
    extend_duration_clicked = pyqtSignal()
    save_animation_clicked = pyqtSignal()
    load_animation_clicked = pyqtSignal()
    export_animation_bin_clicked = pyqtSignal()
    solid_bg_enabled_changed = pyqtSignal(bool)
    solid_bg_color_changed = pyqtSignal(int, int, int, int)
    solid_bg_auto_requested = pyqtSignal()
    sprite_assign_clicked = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Create scrollable container
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        # Main widget inside scroll area
        scroll_widget = QWidget()
        self.main_layout = QVBoxLayout(scroll_widget)
        
        self.init_ui()
        
        scroll.setWidget(scroll_widget)
        
        # Set up the main layout
        container_layout = QVBoxLayout(self)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.addWidget(scroll, stretch=1)
        self._preferred_width = 440
        self._max_width = 600
        size_policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.setSizePolicy(size_policy)
        self.setMaximumWidth(self._max_width)

    def sizeHint(self):
        """Provide a comfortable default width without blocking collapse."""
        base_hint = super().sizeHint()
        preferred_width = getattr(self, "_preferred_width", 360)
        max_width = getattr(self, "_max_width", 0)
        if not base_hint.isValid():
            return QSize(preferred_width, 0)
        width = max(base_hint.width(), preferred_width)
        if max_width:
            width = min(width, max_width)
        base_hint.setWidth(width)
        return base_hint
    
    def init_ui(self):
        """Initialize the UI"""
        # File selection
        file_group = QGroupBox("File Selection")
        file_layout = QVBoxLayout()

        self.barebones_container = QWidget()
        barebones_layout = QVBoxLayout(self.barebones_container)
        barebones_layout.setContentsMargins(0, 0, 0, 0)

        search_layout = QHBoxLayout()
        search_label = QLabel("Search:")
        search_layout.addWidget(search_label)

        self.file_search_input = QLineEdit()
        self.file_search_input.setPlaceholderText("Filter BIN/JSON files...")
        self.file_search_input.setClearButtonEnabled(True)
        self.file_search_input.textChanged.connect(self.file_search_changed.emit)
        search_layout.addWidget(self.file_search_input)

        barebones_layout.addLayout(search_layout)
        
        self.bin_combo = FullscreenSafeComboBox()
        self.bin_combo.currentIndexChanged.connect(self.bin_selected.emit)
        barebones_layout.addWidget(QLabel("Select BIN/JSON:"))
        barebones_layout.addWidget(self.bin_combo)

        self.file_count_label = QLabel("No files indexed")
        self.file_count_label.setStyleSheet("color: gray; font-size: 8pt;")
        barebones_layout.addWidget(self.file_count_label)
        
        convert_btn = QPushButton("Convert BIN to JSON")
        convert_btn.clicked.connect(self.convert_bin_clicked.emit)
        barebones_layout.addWidget(convert_btn)
        
        refresh_btn = QPushButton("Refresh File List")
        refresh_btn.clicked.connect(self.refresh_files_clicked.emit)
        barebones_layout.addWidget(refresh_btn)

        file_layout.addWidget(self.barebones_container)

        self.monster_browser_container = QWidget()
        monster_layout = QVBoxLayout(self.monster_browser_container)
        monster_layout.setContentsMargins(0, 0, 0, 0)
        browser_label = QLabel("Use the Monster Browser to visually select monster files.")
        browser_label.setWordWrap(True)
        monster_layout.addWidget(browser_label)
        self.monster_browser_button = QPushButton("Open Monster Browser")
        self.monster_browser_button.clicked.connect(self.monster_browser_requested.emit)
        monster_layout.addWidget(self.monster_browser_button)
        self.monster_browser_hint = QLabel(
            "Portraits load from data/gfx/book (non-silhouette images only)."
        )
        self.monster_browser_hint.setStyleSheet("color: gray; font-size: 8pt;")
        self.monster_browser_hint.setWordWrap(True)
        monster_layout.addWidget(self.monster_browser_hint)
        file_layout.addWidget(self.monster_browser_container)
        
        file_group.setLayout(file_layout)
        self.main_layout.addWidget(file_group)
        self.set_barebones_file_mode(True)
        
        # Animation selection
        anim_group = QGroupBox("Animation")
        anim_layout = QVBoxLayout()
        
        self.anim_combo = FullscreenSafeComboBox()
        self.anim_combo.currentIndexChanged.connect(self.animation_selected.emit)
        anim_layout.addWidget(QLabel("Select Animation:"))
        anim_layout.addWidget(self.anim_combo)

        self.costume_combo = FullscreenSafeComboBox()
        self.costume_combo.currentIndexChanged.connect(self.costume_selected.emit)
        anim_layout.addWidget(QLabel("Select Costume:"))
        anim_layout.addWidget(self.costume_combo)
        self.costume_convert_btn = QPushButton("Convert Costume BIN to JSON")
        self.costume_convert_btn.clicked.connect(self.costume_convert_clicked.emit)
        anim_layout.addWidget(self.costume_convert_btn)

        pose_row = QHBoxLayout()
        pose_row.setSpacing(6)
        pose_row.addWidget(QLabel("Pose Influence:"))
        self.pose_mode_combo = FullscreenSafeComboBox()
        self.pose_mode_combo.addItem("Keyframe Only", "current")
        self.pose_mode_combo.addItem("Propagate Forward", "forward")
        self.pose_mode_combo.currentIndexChanged.connect(
            lambda idx: self.pose_mode_changed.emit(self.pose_mode_combo.itemData(idx))
        )
        pose_row.addWidget(self.pose_mode_combo, 1)
        self.record_pose_btn = QPushButton("Record Pose")
        self.record_pose_btn.setToolTip("Bake current gizmo offsets into animation keyframes")
        self.record_pose_btn.clicked.connect(self.pose_record_clicked.emit)
        pose_row.addWidget(self.record_pose_btn)
        anim_layout.addLayout(pose_row)

        pose_actions = QHBoxLayout()
        pose_actions.setSpacing(6)
        self.reset_pose_btn = QPushButton("Reset Pose")
        self.reset_pose_btn.setToolTip("Revert selected keyframes to their default animation values")
        self.reset_pose_btn.clicked.connect(self.pose_reset_clicked.emit)
        pose_actions.addWidget(self.reset_pose_btn)
        self.undo_keyframe_btn = QPushButton("Undo Keyframe")
        self.undo_keyframe_btn.setToolTip("Undo the most recent keyframe edit")
        self.undo_keyframe_btn.clicked.connect(self.keyframe_undo_clicked.emit)
        pose_actions.addWidget(self.undo_keyframe_btn)
        self.redo_keyframe_btn = QPushButton("Redo Keyframe")
        self.redo_keyframe_btn.setToolTip("Redo the last undone keyframe edit")
        self.redo_keyframe_btn.clicked.connect(self.keyframe_redo_clicked.emit)
        pose_actions.addWidget(self.redo_keyframe_btn)
        self.delete_other_keyframes_btn = QPushButton("Delete Other Keyframes")
        self.delete_other_keyframes_btn.setToolTip("Remove all keyframes except those at the current time for selected layers")
        self.delete_other_keyframes_btn.clicked.connect(self.keyframe_delete_others_clicked.emit)
        pose_actions.addWidget(self.delete_other_keyframes_btn)
        self.extend_duration_btn = QPushButton("Set Duration…")
        self.extend_duration_btn.setToolTip("Adjust total animation length")
        self.extend_duration_btn.clicked.connect(self.extend_duration_clicked.emit)
        pose_actions.addWidget(self.extend_duration_btn)
        io_row = QHBoxLayout()
        io_row.setSpacing(6)
        self.load_animation_btn = QPushButton("Load Animation…")
        self.load_animation_btn.setToolTip("Load a previously saved animation JSON file")
        self.load_animation_btn.clicked.connect(self.load_animation_clicked.emit)
        io_row.addWidget(self.load_animation_btn)
        self.save_animation_btn = QPushButton("Save Animation…")
        self.save_animation_btn.setToolTip("Save the current animation (layers + keyframes) to a JSON file")
        self.save_animation_btn.clicked.connect(self.save_animation_clicked.emit)
        io_row.addWidget(self.save_animation_btn)
        self.export_bin_btn = QPushButton("Export Animation BIN…")
        self.export_bin_btn.setToolTip("Package the current animation into a BIN file usable by the game")
        self.export_bin_btn.clicked.connect(self.export_animation_bin_clicked.emit)
        io_row.addWidget(self.export_bin_btn)
        anim_layout.addLayout(io_row)
        anim_layout.addLayout(pose_actions)
        
        anim_group.setLayout(anim_layout)
        self.main_layout.addWidget(anim_group)
        self.update_costume_options([])

        # Sprite variation controls
        sprite_group = QGroupBox("Sprite Variations")
        sprite_layout = QVBoxLayout()
        self.sprite_assign_hint = QLabel(
            "Select layer(s) and timeline keyframes, then choose a sprite to apply."
        )
        self.sprite_assign_hint.setWordWrap(True)
        self.sprite_assign_hint.setStyleSheet("color: gray; font-size: 9pt;")
        sprite_layout.addWidget(self.sprite_assign_hint)
        self.assign_sprite_btn = QPushButton("Assign Sprite…")
        self.assign_sprite_btn.setToolTip(
            "Applies a sprite from the active atlas to the selected keyframe markers."
        )
        self.assign_sprite_btn.clicked.connect(self.sprite_assign_clicked.emit)
        sprite_layout.addWidget(self.assign_sprite_btn)
        sprite_group.setLayout(sprite_layout)
        self.main_layout.addWidget(sprite_group)
        self.set_sprite_tools_enabled(False)
        
        # Render settings
        render_group = QGroupBox("Render Settings")
        render_layout = QVBoxLayout()
        
        scale_layout = QHBoxLayout()
        scale_layout.addWidget(QLabel("Scale:"))
        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setMinimum(0.1)
        self.scale_spin.setMaximum(10.0)
        self.scale_spin.setValue(1.0)
        self.scale_spin.setSingleStep(0.1)
        self.scale_spin.valueChanged.connect(self.scale_changed.emit)
        scale_layout.addWidget(self.scale_spin)
        render_layout.addLayout(scale_layout)
        
        fps_layout = QHBoxLayout()
        fps_layout.addWidget(QLabel("FPS:"))
        self.fps_spin = QSpinBox()
        self.fps_spin.setMinimum(1)
        self.fps_spin.setMaximum(120)
        self.fps_spin.setValue(60)
        self.fps_spin.valueChanged.connect(self.fps_changed.emit)
        fps_layout.addWidget(self.fps_spin)
        render_layout.addLayout(fps_layout)
        
        # Position scale slider
        pos_scale_layout = QHBoxLayout()
        pos_scale_layout.addWidget(QLabel("Position Scale:"))
        self.pos_scale_spin = QDoubleSpinBox()
        self.pos_scale_spin.setMinimum(-1000.0)  # Unlimited range
        self.pos_scale_spin.setMaximum(1000.0)   # Unlimited range
        self.pos_scale_spin.setValue(1.0)
        self.pos_scale_spin.setSingleStep(0.01)
        self.pos_scale_spin.setDecimals(3)
        self.pos_scale_spin.valueChanged.connect(self.position_scale_changed.emit)
        pos_scale_layout.addWidget(self.pos_scale_spin)
        render_layout.addLayout(pos_scale_layout)
        
        self.pos_scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.pos_scale_slider.setMinimum(-100000)  # -1000.0
        self.pos_scale_slider.setMaximum(100000)   # 1000.0
        self.pos_scale_slider.setValue(100)        # 1.0
        self.pos_scale_slider.valueChanged.connect(self.position_scale_slider_changed.emit)
        render_layout.addWidget(self.pos_scale_slider)
        
        pos_scale_help = QLabel("Adjusts spacing between sprite segments")
        pos_scale_help.setStyleSheet("color: gray; font-size: 8pt; font-style: italic;")
        render_layout.addWidget(pos_scale_help)
        
        # Base World Scale slider (from Ghidra analysis)
        base_scale_layout = QHBoxLayout()
        base_scale_layout.addWidget(QLabel("Base World Scale:"))
        self.base_scale_spin = QDoubleSpinBox()
        self.base_scale_spin.setMinimum(-20.0)
        self.base_scale_spin.setMaximum(20.0)
        self.base_scale_spin.setValue(1.0)
        self.base_scale_spin.setSingleStep(0.1)
        self.base_scale_spin.setDecimals(2)
        self.base_scale_spin.valueChanged.connect(self.base_world_scale_changed.emit)
        base_scale_layout.addWidget(self.base_scale_spin)
        render_layout.addLayout(base_scale_layout)
        
        self.base_scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.base_scale_slider.setMinimum(-2000)  # -20.0
        self.base_scale_slider.setMaximum(2000)   # 20.0
        self.base_scale_slider.setValue(100)      # 1.0
        self.base_scale_slider.valueChanged.connect(self.base_world_scale_slider_changed.emit)
        render_layout.addWidget(self.base_scale_slider)
        
        base_scale_help = QLabel("Converts JSON coordinates to screen space (from Ghidra analysis)")
        base_scale_help.setStyleSheet("color: gray; font-size: 8pt; font-style: italic;")
        render_layout.addWidget(base_scale_help)

        placement_label = QLabel("Sprite Placement Adjustments")
        placement_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        render_layout.addWidget(placement_label)

        # Drag speed controls
        drag_speed_layout = QHBoxLayout()
        drag_speed_layout.addWidget(QLabel("Drag Speed:"))
        self.translation_spin = QDoubleSpinBox()
        self.translation_spin.setMinimum(0.01)
        self.translation_spin.setMaximum(5.0)
        self.translation_spin.setDecimals(2)
        self.translation_spin.setSingleStep(0.01)
        self.translation_spin.setValue(1.0)
        drag_speed_layout.addWidget(self.translation_spin)
        render_layout.addLayout(drag_speed_layout)

        self.translation_slider = QSlider(Qt.Orientation.Horizontal)
        self.translation_slider.setMinimum(1)   # 0.01
        self.translation_slider.setMaximum(500) # 5.00
        self.translation_slider.setValue(100)   # 1.00
        render_layout.addWidget(self.translation_slider)

        # Rotation drag controls
        rotation_speed_layout = QHBoxLayout()
        rotation_speed_layout.addWidget(QLabel("Rotation Speed:"))
        self.rotation_spin = QDoubleSpinBox()
        self.rotation_spin.setMinimum(0.1)
        self.rotation_spin.setMaximum(20.0)
        self.rotation_spin.setDecimals(1)
        self.rotation_spin.setSingleStep(0.1)
        self.rotation_spin.setValue(1.0)
        rotation_speed_layout.addWidget(self.rotation_spin)
        render_layout.addLayout(rotation_speed_layout)

        self.rotation_slider = QSlider(Qt.Orientation.Horizontal)
        self.rotation_slider.setMinimum(1)   # 0.1
        self.rotation_slider.setMaximum(200) # 20.0
        self.rotation_slider.setValue(10)    # 1.0
        render_layout.addWidget(self.rotation_slider)

        # Rotation overlay sizing
        overlay_layout = QHBoxLayout()
        overlay_layout.addWidget(QLabel("Rotation Gizmo Size:"))
        self.rotation_overlay_spin = QDoubleSpinBox()
        self.rotation_overlay_spin.setMinimum(10.0)
        self.rotation_overlay_spin.setMaximum(500.0)
        self.rotation_overlay_spin.setDecimals(1)
        self.rotation_overlay_spin.setSingleStep(5.0)
        self.rotation_overlay_spin.setValue(120.0)
        overlay_layout.addWidget(self.rotation_overlay_spin)
        render_layout.addLayout(overlay_layout)

        self.rotation_overlay_slider = QSlider(Qt.Orientation.Horizontal)
        self.rotation_overlay_slider.setMinimum(10)
        self.rotation_overlay_slider.setMaximum(500)
        self.rotation_overlay_slider.setValue(120)
        render_layout.addWidget(self.rotation_overlay_slider)

        self.rotation_gizmo_checkbox = QCheckBox("Show Rotation Gizmo Overlay")
        self.rotation_gizmo_checkbox.setToolTip("Display a draggable ring around the selected sprite for rotation")
        render_layout.addWidget(self.rotation_gizmo_checkbox)

        rotation_help = QLabel("Use the overlay ring to rotate sprites after selecting them.")
        rotation_help.setStyleSheet("color: gray; font-size: 8pt; font-style: italic;")
        render_layout.addWidget(rotation_help)

        bpm_header = QLabel("Animation BPM")
        bpm_header.setStyleSheet("font-weight: bold; margin-top: 10px;")
        render_layout.addWidget(bpm_header)

        bpm_layout = QHBoxLayout()
        bpm_layout.addWidget(QLabel("BPM:"))
        self.bpm_spin = QDoubleSpinBox()
        self.bpm_spin.setRange(20.0, 300.0)
        self.bpm_spin.setDecimals(1)
        self.bpm_spin.setSingleStep(0.5)
        self.bpm_spin.setValue(120.0)
        bpm_layout.addWidget(self.bpm_spin)
        render_layout.addLayout(bpm_layout)

        self.bpm_slider = QSlider(Qt.Orientation.Horizontal)
        self.bpm_slider.setMinimum(200)   # 20.0 BPM
        self.bpm_slider.setMaximum(3000)  # 300.0 BPM
        self.bpm_slider.setValue(1200)    # 120.0 BPM
        render_layout.addWidget(self.bpm_slider)

        bpm_help = QLabel("Derived from the island MIDI tempo. Adjust to fine-tune playback speed.")
        bpm_help.setStyleSheet("color: gray; font-size: 8pt; font-style: italic;")
        render_layout.addWidget(bpm_help)

        bpm_toggle_layout = QHBoxLayout()
        self.sync_audio_checkbox = QCheckBox("Sync Audio Speed to BPM")
        bpm_toggle_layout.addWidget(self.sync_audio_checkbox)
        self.pitch_shift_checkbox = QCheckBox("Pitch Shift Audio")
        self.pitch_shift_checkbox.setToolTip("Enable to let the audio pitch rise/fall alongside the BPM tempo.")
        bpm_toggle_layout.addWidget(self.pitch_shift_checkbox)
        render_layout.addLayout(bpm_toggle_layout)

        self.reset_bpm_button = QPushButton("Reset BPM to Default")
        render_layout.addWidget(self.reset_bpm_button)
        self.lock_bpm_button = QPushButton("Lock Base BPM…")
        render_layout.addWidget(self.lock_bpm_button)

        scale_header = QLabel("Scale Gizmo")
        scale_header.setStyleSheet("font-weight: bold; margin-top: 10px;")
        render_layout.addWidget(scale_header)

        self.scale_gizmo_checkbox = QCheckBox("Show Scale Gizmo Overlay")
        self.scale_gizmo_checkbox.toggled.connect(self.scale_gizmo_toggled.emit)
        render_layout.addWidget(self.scale_gizmo_checkbox)

        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("Scale Mode:"))
        self.scale_mode_combo = QComboBox()
        self.scale_mode_combo.addItems(["Uniform", "Per-Axis"])
        self.scale_mode_combo.currentTextChanged.connect(self.scale_gizmo_mode_changed.emit)
        mode_layout.addWidget(self.scale_mode_combo)
        render_layout.addLayout(mode_layout)

        scale_help = QLabel("Uniform scales evenly; Per-Axis lets you stretch horizontally/vertically.")
        scale_help.setStyleSheet("color: gray; font-size: 8pt; font-style: italic;")
        render_layout.addWidget(scale_help)

        overlay_header = QLabel("Anchor & Parent Controls")
        overlay_header.setStyleSheet("font-weight: bold; margin-top: 10px;")
        render_layout.addWidget(overlay_header)

        self.anchor_overlay_checkbox = QCheckBox("Show Anchor Overlay / Edit Anchors")
        self.anchor_overlay_checkbox.setToolTip("Displays every layer's anchor pivot and lets you drag them live.")
        self.anchor_overlay_checkbox.toggled.connect(self.anchor_overlay_toggled.emit)
        render_layout.addWidget(self.anchor_overlay_checkbox)

        self.parent_overlay_checkbox = QCheckBox("Show Parent Overlay / Parent Handles")
        self.parent_overlay_checkbox.setToolTip("Shows parent-child connectors and lets you drag parent handles to reposition hierarchies.")
        self.parent_overlay_checkbox.toggled.connect(self.parent_overlay_toggled.emit)
        render_layout.addWidget(self.parent_overlay_checkbox)

        anchor_precision_layout = QHBoxLayout()
        anchor_precision_layout.addWidget(QLabel("Anchor Drag Precision:"))
        self.anchor_precision_spin = QDoubleSpinBox()
        self.anchor_precision_spin.setRange(0.01, 2.0)
        self.anchor_precision_spin.setDecimals(2)
        self.anchor_precision_spin.setSingleStep(0.01)
        self.anchor_precision_spin.setValue(0.25)
        anchor_precision_layout.addWidget(self.anchor_precision_spin)
        render_layout.addLayout(anchor_precision_layout)

        self.anchor_precision_slider = QSlider(Qt.Orientation.Horizontal)
        self.anchor_precision_slider.setRange(1, 200)  # 0.01 to 2.00
        self.anchor_precision_slider.setValue(25)      # 0.25 default
        render_layout.addWidget(self.anchor_precision_slider)

        anchor_precision_help = QLabel("Smaller values move anchors in finer increments while dragging.")
        anchor_precision_help.setStyleSheet("color: gray; font-size: 8pt; font-style: italic;")
        render_layout.addWidget(anchor_precision_help)

        # Advanced placement bias controls
        bias_header = QLabel("Advanced Placement Bias")
        bias_header.setStyleSheet("font-weight: bold; margin-top: 10px;")
        render_layout.addWidget(bias_header)

        # Anchor bias X
        anchor_bias_x_layout = QHBoxLayout()
        anchor_bias_x_layout.addWidget(QLabel("Anchor Bias X:"))
        self.anchor_bias_x_spin = QDoubleSpinBox()
        self.anchor_bias_x_spin.setRange(-500.0, 500.0)
        self.anchor_bias_x_spin.setDecimals(2)
        self.anchor_bias_x_spin.setSingleStep(0.1)
        self.anchor_bias_x_spin.setValue(0.0)
        anchor_bias_x_layout.addWidget(self.anchor_bias_x_spin)
        render_layout.addLayout(anchor_bias_x_layout)

        self.anchor_bias_x_slider = QSlider(Qt.Orientation.Horizontal)
        self.anchor_bias_x_slider.setRange(-50000, 50000)  # -500.00 to 500.00
        self.anchor_bias_x_slider.setValue(0)
        render_layout.addWidget(self.anchor_bias_x_slider)

        # Anchor bias Y
        anchor_bias_y_layout = QHBoxLayout()
        anchor_bias_y_layout.addWidget(QLabel("Anchor Bias Y:"))
        self.anchor_bias_y_spin = QDoubleSpinBox()
        self.anchor_bias_y_spin.setRange(-500.0, 500.0)
        self.anchor_bias_y_spin.setDecimals(2)
        self.anchor_bias_y_spin.setSingleStep(0.1)
        self.anchor_bias_y_spin.setValue(0.0)
        anchor_bias_y_layout.addWidget(self.anchor_bias_y_spin)
        render_layout.addLayout(anchor_bias_y_layout)

        self.anchor_bias_y_slider = QSlider(Qt.Orientation.Horizontal)
        self.anchor_bias_y_slider.setRange(-50000, 50000)
        self.anchor_bias_y_slider.setValue(0)
        render_layout.addWidget(self.anchor_bias_y_slider)

        # Local position multiplier
        local_pos_layout = QHBoxLayout()
        local_pos_layout.addWidget(QLabel("Local Pos Multiplier:"))
        self.local_pos_spin = QDoubleSpinBox()
        self.local_pos_spin.setRange(0.0, 5.0)
        self.local_pos_spin.setDecimals(2)
        self.local_pos_spin.setSingleStep(0.05)
        self.local_pos_spin.setValue(1.0)
        local_pos_layout.addWidget(self.local_pos_spin)
        render_layout.addLayout(local_pos_layout)

        self.local_pos_slider = QSlider(Qt.Orientation.Horizontal)
        self.local_pos_slider.setRange(0, 500)  # 0.00 to 5.00
        self.local_pos_slider.setValue(100)
        render_layout.addWidget(self.local_pos_slider)

        # Parent mix
        parent_mix_layout = QHBoxLayout()
        parent_mix_layout.addWidget(QLabel("Parent Mix:"))
        self.parent_mix_spin = QDoubleSpinBox()
        self.parent_mix_spin.setRange(0.0, 1.0)
        self.parent_mix_spin.setDecimals(2)
        self.parent_mix_spin.setSingleStep(0.01)
        self.parent_mix_spin.setValue(1.0)
        parent_mix_layout.addWidget(self.parent_mix_spin)
        render_layout.addLayout(parent_mix_layout)

        self.parent_mix_slider = QSlider(Qt.Orientation.Horizontal)
        self.parent_mix_slider.setRange(0, 100)
        self.parent_mix_slider.setValue(100)
        render_layout.addWidget(self.parent_mix_slider)

        # Rotation bias
        rotation_bias_layout = QHBoxLayout()
        rotation_bias_layout.addWidget(QLabel("Rotation Bias (°):"))
        self.rotation_bias_spin = QDoubleSpinBox()
        self.rotation_bias_spin.setRange(-360.0, 360.0)
        self.rotation_bias_spin.setDecimals(1)
        self.rotation_bias_spin.setSingleStep(1.0)
        self.rotation_bias_spin.setValue(0.0)
        rotation_bias_layout.addWidget(self.rotation_bias_spin)
        render_layout.addLayout(rotation_bias_layout)

        self.rotation_bias_slider = QSlider(Qt.Orientation.Horizontal)
        self.rotation_bias_slider.setRange(-3600, 3600)  # -360.0 to 360.0
        self.rotation_bias_slider.setValue(0)
        render_layout.addWidget(self.rotation_bias_slider)

        # Scale bias X
        scale_bias_x_layout = QHBoxLayout()
        scale_bias_x_layout.addWidget(QLabel("Scale Bias X:"))
        self.scale_bias_x_spin = QDoubleSpinBox()
        self.scale_bias_x_spin.setRange(0.0, 5.0)
        self.scale_bias_x_spin.setDecimals(2)
        self.scale_bias_x_spin.setSingleStep(0.05)
        self.scale_bias_x_spin.setValue(1.0)
        scale_bias_x_layout.addWidget(self.scale_bias_x_spin)
        render_layout.addLayout(scale_bias_x_layout)

        self.scale_bias_x_slider = QSlider(Qt.Orientation.Horizontal)
        self.scale_bias_x_slider.setRange(0, 500)
        self.scale_bias_x_slider.setValue(100)
        render_layout.addWidget(self.scale_bias_x_slider)

        # Scale bias Y
        scale_bias_y_layout = QHBoxLayout()
        scale_bias_y_layout.addWidget(QLabel("Scale Bias Y:"))
        self.scale_bias_y_spin = QDoubleSpinBox()
        self.scale_bias_y_spin.setRange(0.0, 5.0)
        self.scale_bias_y_spin.setDecimals(2)
        self.scale_bias_y_spin.setSingleStep(0.05)
        self.scale_bias_y_spin.setValue(1.0)
        scale_bias_y_layout.addWidget(self.scale_bias_y_spin)
        render_layout.addLayout(scale_bias_y_layout)

        self.scale_bias_y_slider = QSlider(Qt.Orientation.Horizontal)
        self.scale_bias_y_slider.setRange(0, 500)
        self.scale_bias_y_slider.setValue(100)
        render_layout.addWidget(self.scale_bias_y_slider)

        # World offset X
        world_offset_x_layout = QHBoxLayout()
        world_offset_x_layout.addWidget(QLabel("World Offset X:"))
        self.world_offset_x_spin = QDoubleSpinBox()
        self.world_offset_x_spin.setRange(-1000.0, 1000.0)
        self.world_offset_x_spin.setDecimals(2)
        self.world_offset_x_spin.setSingleStep(1.0)
        self.world_offset_x_spin.setValue(0.0)
        world_offset_x_layout.addWidget(self.world_offset_x_spin)
        render_layout.addLayout(world_offset_x_layout)

        self.world_offset_x_slider = QSlider(Qt.Orientation.Horizontal)
        self.world_offset_x_slider.setRange(-100000, 100000)  # -1000.00 to 1000.00
        self.world_offset_x_slider.setValue(0)
        render_layout.addWidget(self.world_offset_x_slider)

        # World offset Y
        world_offset_y_layout = QHBoxLayout()
        world_offset_y_layout.addWidget(QLabel("World Offset Y:"))
        self.world_offset_y_spin = QDoubleSpinBox()
        self.world_offset_y_spin.setRange(-1000.0, 1000.0)
        self.world_offset_y_spin.setDecimals(2)
        self.world_offset_y_spin.setSingleStep(1.0)
        self.world_offset_y_spin.setValue(0.0)
        world_offset_y_layout.addWidget(self.world_offset_y_spin)
        render_layout.addLayout(world_offset_y_layout)

        self.world_offset_y_slider = QSlider(Qt.Orientation.Horizontal)
        self.world_offset_y_slider.setRange(-100000, 100000)
        self.world_offset_y_slider.setValue(0)
        render_layout.addWidget(self.world_offset_y_slider)

        # Trim shift multiplier
        trim_shift_layout = QHBoxLayout()
        trim_shift_layout.addWidget(QLabel("Trim Shift Multiplier:"))
        self.trim_shift_spin = QDoubleSpinBox()
        self.trim_shift_spin.setRange(0.0, 5.0)
        self.trim_shift_spin.setDecimals(2)
        self.trim_shift_spin.setSingleStep(0.05)
        self.trim_shift_spin.setValue(1.0)
        trim_shift_layout.addWidget(self.trim_shift_spin)
        render_layout.addLayout(trim_shift_layout)

        self.trim_shift_slider = QSlider(Qt.Orientation.Horizontal)
        self.trim_shift_slider.setRange(0, 500)
        self.trim_shift_slider.setValue(100)
        render_layout.addWidget(self.trim_shift_slider)

        reset_bias_btn = QPushButton("Reset Placement Bias Settings")
        reset_bias_btn.clicked.connect(self.reset_placement_bias_settings)
        render_layout.addWidget(reset_bias_btn)
        
        # Camera control buttons in a row
        camera_btn_layout = QHBoxLayout()
        
        reset_camera_btn = QPushButton("Reset Camera")
        reset_camera_btn.clicked.connect(self.reset_camera_clicked.emit)
        camera_btn_layout.addWidget(reset_camera_btn)
        
        fit_to_view_btn = QPushButton("Fit to View")
        fit_to_view_btn.clicked.connect(self.fit_to_view_clicked.emit)
        fit_to_view_btn.setToolTip("Center and scale to fit the monster in view")
        camera_btn_layout.addWidget(fit_to_view_btn)
        
        render_layout.addLayout(camera_btn_layout)
        
        camera_help = QLabel("Right-click/Middle-click + drag to pan\nScroll wheel to zoom")
        camera_help.setStyleSheet("color: gray; font-size: 9pt;")
        render_layout.addWidget(camera_help)
        
        # Debug/Visualization options
        render_layout.addWidget(QLabel(""))  # Spacer
        viz_label = QLabel("Visualization:")
        viz_label.setStyleSheet("font-weight: bold;")
        render_layout.addWidget(viz_label)
        
        self.show_bones_checkbox = QCheckBox("Show Bone Overlay")
        self.show_bones_checkbox.setToolTip("Display skeleton hierarchy with bones and anchor points")
        self.show_bones_checkbox.toggled.connect(self.show_bones_toggled.emit)
        render_layout.addWidget(self.show_bones_checkbox)

        self.antialias_checkbox = QCheckBox("Enable Anti-Aliasing")
        self.antialias_checkbox.setToolTip("Toggle multi-sample anti-aliasing for smoother edges")
        self.antialias_checkbox.setChecked(True)
        self.antialias_checkbox.toggled.connect(self.antialias_toggled.emit)
        render_layout.addWidget(self.antialias_checkbox)
        
        # Tweening toggle: enable/disable linear interpolation
        self.tweening_checkbox = QCheckBox("Enable Tweening (Linear Interpolation)")
        self.tweening_checkbox.setToolTip("When disabled, values snap to the previous keyframe (no tweening)")
        self.tweening_checkbox.setChecked(True)
        self.tweening_checkbox.toggled.connect(self.tweening_toggled.emit)
        render_layout.addWidget(self.tweening_checkbox)
        
        bones_help = QLabel("Shows parent-child connections and anchor points")
        bones_help.setStyleSheet("color: gray; font-size: 8pt; font-style: italic;")
        render_layout.addWidget(bones_help)
        
        render_group.setLayout(render_layout)
        self.main_layout.addWidget(render_group)
        
        # Sprite dragging controls
        drag_group = QGroupBox("Sprite Dragging")
        drag_layout = QVBoxLayout()
        
        drag_help = QLabel("Left-click + drag to move sprites\nSelect a layer to drag only that layer")
        drag_help.setStyleSheet("color: gray; font-size: 9pt;")
        drag_layout.addWidget(drag_help)
        
        reset_offsets_btn = QPushButton("Reset All Offsets")
        reset_offsets_btn.clicked.connect(self.reset_offsets_clicked.emit)
        drag_layout.addWidget(reset_offsets_btn)

        # Pixel-based nudging controls
        nudge_header = QLabel("Pixel Nudging (Selected Layers)")
        nudge_header.setStyleSheet("font-weight: bold; margin-top: 8px;")
        drag_layout.addWidget(nudge_header)

        nudge_help = QLabel("Adjust selected sprite segments by exact pixel amounts")
        nudge_help.setStyleSheet("color: gray; font-size: 8pt; font-style: italic;")
        drag_layout.addWidget(nudge_help)

        # Nudge step size
        nudge_step_layout = QHBoxLayout()
        nudge_step_layout.addWidget(QLabel("Step Size:"))
        self.nudge_step_spin = QDoubleSpinBox()
        self.nudge_step_spin.setRange(0.1, 100.0)
        self.nudge_step_spin.setDecimals(1)
        self.nudge_step_spin.setSingleStep(0.5)
        self.nudge_step_spin.setValue(1.0)
        self.nudge_step_spin.setToolTip("Amount to nudge per button click (pixels)")
        nudge_step_layout.addWidget(self.nudge_step_spin)
        nudge_step_layout.addWidget(QLabel("px"))
        nudge_step_layout.addStretch()
        drag_layout.addLayout(nudge_step_layout)

        # X/Y Position nudging
        pos_nudge_layout = QHBoxLayout()
        pos_nudge_layout.setSpacing(4)
        pos_nudge_layout.addWidget(QLabel("Position:"))
        
        self.nudge_x_minus_btn = QPushButton("← X")
        self.nudge_x_minus_btn.setFixedWidth(50)
        self.nudge_x_minus_btn.setToolTip("Move selected layers left")
        self.nudge_x_minus_btn.clicked.connect(lambda: self._emit_nudge_x(-1))
        pos_nudge_layout.addWidget(self.nudge_x_minus_btn)
        
        self.nudge_x_plus_btn = QPushButton("X →")
        self.nudge_x_plus_btn.setFixedWidth(50)
        self.nudge_x_plus_btn.setToolTip("Move selected layers right")
        self.nudge_x_plus_btn.clicked.connect(lambda: self._emit_nudge_x(1))
        pos_nudge_layout.addWidget(self.nudge_x_plus_btn)
        
        pos_nudge_layout.addSpacing(8)
        
        self.nudge_y_minus_btn = QPushButton("↑ Y")
        self.nudge_y_minus_btn.setFixedWidth(50)
        self.nudge_y_minus_btn.setToolTip("Move selected layers up")
        self.nudge_y_minus_btn.clicked.connect(lambda: self._emit_nudge_y(-1))
        pos_nudge_layout.addWidget(self.nudge_y_minus_btn)
        
        self.nudge_y_plus_btn = QPushButton("Y ↓")
        self.nudge_y_plus_btn.setFixedWidth(50)
        self.nudge_y_plus_btn.setToolTip("Move selected layers down")
        self.nudge_y_plus_btn.clicked.connect(lambda: self._emit_nudge_y(1))
        pos_nudge_layout.addWidget(self.nudge_y_plus_btn)
        
        pos_nudge_layout.addStretch()
        drag_layout.addLayout(pos_nudge_layout)

        # Rotation nudging
        rot_nudge_layout = QHBoxLayout()
        rot_nudge_layout.setSpacing(4)
        rot_nudge_layout.addWidget(QLabel("Rotation:"))
        
        self.nudge_rot_step_spin = QDoubleSpinBox()
        self.nudge_rot_step_spin.setRange(0.1, 90.0)
        self.nudge_rot_step_spin.setDecimals(1)
        self.nudge_rot_step_spin.setSingleStep(1.0)
        self.nudge_rot_step_spin.setValue(5.0)
        self.nudge_rot_step_spin.setToolTip("Rotation step in degrees")
        self.nudge_rot_step_spin.setFixedWidth(60)
        rot_nudge_layout.addWidget(self.nudge_rot_step_spin)
        rot_nudge_layout.addWidget(QLabel("°"))
        
        self.nudge_rot_minus_btn = QPushButton("↺ CCW")
        self.nudge_rot_minus_btn.setFixedWidth(60)
        self.nudge_rot_minus_btn.setToolTip("Rotate counter-clockwise")
        self.nudge_rot_minus_btn.clicked.connect(lambda: self._emit_nudge_rotation(-1))
        rot_nudge_layout.addWidget(self.nudge_rot_minus_btn)
        
        self.nudge_rot_plus_btn = QPushButton("CW ↻")
        self.nudge_rot_plus_btn.setFixedWidth(60)
        self.nudge_rot_plus_btn.setToolTip("Rotate clockwise")
        self.nudge_rot_plus_btn.clicked.connect(lambda: self._emit_nudge_rotation(1))
        rot_nudge_layout.addWidget(self.nudge_rot_plus_btn)
        
        rot_nudge_layout.addStretch()
        drag_layout.addLayout(rot_nudge_layout)

        # Scale nudging
        scale_nudge_layout = QHBoxLayout()
        scale_nudge_layout.setSpacing(4)
        scale_nudge_layout.addWidget(QLabel("Scale:"))
        
        self.nudge_scale_step_spin = QDoubleSpinBox()
        self.nudge_scale_step_spin.setRange(0.01, 1.0)
        self.nudge_scale_step_spin.setDecimals(2)
        self.nudge_scale_step_spin.setSingleStep(0.01)
        self.nudge_scale_step_spin.setValue(0.05)
        self.nudge_scale_step_spin.setToolTip("Scale step multiplier")
        self.nudge_scale_step_spin.setFixedWidth(60)
        scale_nudge_layout.addWidget(self.nudge_scale_step_spin)
        
        self.nudge_scale_minus_btn = QPushButton("−")
        self.nudge_scale_minus_btn.setFixedWidth(35)
        self.nudge_scale_minus_btn.setToolTip("Decrease scale (uniform)")
        self.nudge_scale_minus_btn.clicked.connect(lambda: self._emit_nudge_scale_uniform(-1))
        scale_nudge_layout.addWidget(self.nudge_scale_minus_btn)
        
        self.nudge_scale_plus_btn = QPushButton("+")
        self.nudge_scale_plus_btn.setFixedWidth(35)
        self.nudge_scale_plus_btn.setToolTip("Increase scale (uniform)")
        self.nudge_scale_plus_btn.clicked.connect(lambda: self._emit_nudge_scale_uniform(1))
        scale_nudge_layout.addWidget(self.nudge_scale_plus_btn)
        
        scale_nudge_layout.addSpacing(8)
        scale_nudge_layout.addWidget(QLabel("X:"))
        
        self.nudge_scale_x_minus_btn = QPushButton("−")
        self.nudge_scale_x_minus_btn.setFixedWidth(30)
        self.nudge_scale_x_minus_btn.setToolTip("Decrease X scale")
        self.nudge_scale_x_minus_btn.clicked.connect(lambda: self._emit_nudge_scale_x(-1))
        scale_nudge_layout.addWidget(self.nudge_scale_x_minus_btn)
        
        self.nudge_scale_x_plus_btn = QPushButton("+")
        self.nudge_scale_x_plus_btn.setFixedWidth(30)
        self.nudge_scale_x_plus_btn.setToolTip("Increase X scale")
        self.nudge_scale_x_plus_btn.clicked.connect(lambda: self._emit_nudge_scale_x(1))
        scale_nudge_layout.addWidget(self.nudge_scale_x_plus_btn)
        
        scale_nudge_layout.addWidget(QLabel("Y:"))
        
        self.nudge_scale_y_minus_btn = QPushButton("−")
        self.nudge_scale_y_minus_btn.setFixedWidth(30)
        self.nudge_scale_y_minus_btn.setToolTip("Decrease Y scale")
        self.nudge_scale_y_minus_btn.clicked.connect(lambda: self._emit_nudge_scale_y(-1))
        scale_nudge_layout.addWidget(self.nudge_scale_y_minus_btn)
        
        self.nudge_scale_y_plus_btn = QPushButton("+")
        self.nudge_scale_y_plus_btn.setFixedWidth(30)
        self.nudge_scale_y_plus_btn.setToolTip("Increase Y scale")
        self.nudge_scale_y_plus_btn.clicked.connect(lambda: self._emit_nudge_scale_y(1))
        scale_nudge_layout.addWidget(self.nudge_scale_y_plus_btn)
        
        scale_nudge_layout.addStretch()
        drag_layout.addLayout(scale_nudge_layout)
        
        drag_layout.addWidget(QLabel("Layer Offsets:"))
        
        # Scrollable area for offset display
        offset_scroll = QScrollArea()
        offset_scroll.setWidgetResizable(True)
        offset_scroll.setMaximumHeight(150)
        
        self.offset_display_widget = QWidget()
        self.offset_display_layout = QVBoxLayout(self.offset_display_widget)
        self.offset_display_layout.addStretch()
        
        offset_scroll.setWidget(self.offset_display_widget)
        drag_layout.addWidget(offset_scroll)
        
        drag_group.setLayout(drag_layout)
        self.main_layout.addWidget(drag_group)
        
        # Export options
        export_group = QGroupBox("Export")
        export_layout = QVBoxLayout()
        
        export_frame_btn = QPushButton("Export Current Frame (PNG)")
        export_frame_btn.clicked.connect(self.export_frame_clicked.emit)
        export_layout.addWidget(export_frame_btn)

        export_sequence_btn = QPushButton("Export Animation Frames (PNG Folder)")
        export_sequence_btn.clicked.connect(self.export_frames_sequence_clicked.emit)
        export_layout.addWidget(export_sequence_btn)
        
        export_psd_btn = QPushButton("Export as PSD")
        export_psd_btn.clicked.connect(self.export_psd_clicked.emit)
        export_layout.addWidget(export_psd_btn)
        
        export_mov_btn = QPushButton("Export as MOV")
        export_mov_btn.clicked.connect(self.export_mov_clicked.emit)
        export_layout.addWidget(export_mov_btn)

        export_mp4_btn = QPushButton("Export as MP4")
        export_mp4_btn.clicked.connect(self.export_mp4_clicked.emit)
        export_layout.addWidget(export_mp4_btn)

        export_webm_btn = QPushButton("Export as WEBM")
        export_webm_btn.clicked.connect(self.export_webm_clicked.emit)
        export_layout.addWidget(export_webm_btn)

        export_gif_btn = QPushButton("Export as GIF")
        export_gif_btn.clicked.connect(self.export_gif_clicked.emit)
        export_layout.addWidget(export_gif_btn)

        self.solid_bg_checkbox = QCheckBox("Fill background with solid color")
        self.solid_bg_checkbox.setToolTip("When enabled, exports are composited over a solid color instead of transparency.")
        self.solid_bg_checkbox.toggled.connect(self._on_solid_bg_toggled)
        export_layout.addWidget(self.solid_bg_checkbox)

        bg_controls = QVBoxLayout()
        bg_controls.setSpacing(4)
        self.solid_bg_color_row = QHBoxLayout()
        self.solid_bg_color_row.setSpacing(6)
        self.solid_bg_color_btn = QPushButton("Pick Color")
        self.solid_bg_color_btn.setFixedWidth(90)
        self.solid_bg_color_btn.clicked.connect(self._on_solid_bg_pick_color)
        self.solid_bg_color_row.addWidget(self.solid_bg_color_btn, 0)
        self.solid_bg_hex_input = QLineEdit()
        self.solid_bg_hex_input.setPlaceholderText("#RRGGBBAA")
        self.solid_bg_hex_input.setMaxLength(9)
        self.solid_bg_hex_input.editingFinished.connect(self._on_solid_bg_hex_changed)
        self.solid_bg_color_row.addWidget(self.solid_bg_hex_input, 1)
        bg_controls.addLayout(self.solid_bg_color_row)

        self.solid_bg_rgba_row = QHBoxLayout()
        self.solid_bg_rgba_row.setSpacing(4)
        self.solid_bg_r_spin = self._make_channel_spinbox("R", self._on_solid_bg_spin_changed)
        self.solid_bg_g_spin = self._make_channel_spinbox("G", self._on_solid_bg_spin_changed)
        self.solid_bg_b_spin = self._make_channel_spinbox("B", self._on_solid_bg_spin_changed)
        self.solid_bg_a_spin = self._make_channel_spinbox("A", self._on_solid_bg_spin_changed)
        for label_text, spin in (("R", self.solid_bg_r_spin), ("G", self.solid_bg_g_spin),
                                 ("B", self.solid_bg_b_spin), ("A", self.solid_bg_a_spin)):
            label = QLabel(label_text)
            label.setStyleSheet("color: #666; font-size: 9pt;")
            self.solid_bg_rgba_row.addWidget(label)
            self.solid_bg_rgba_row.addWidget(spin)
        bg_controls.addLayout(self.solid_bg_rgba_row)

        self.solid_bg_auto_btn = QPushButton("Suggest Unique Color")
        self.solid_bg_auto_btn.setToolTip("Attempts to find a color not present in the current monster's textures.")
        self.solid_bg_auto_btn.clicked.connect(self.solid_bg_auto_requested.emit)
        bg_controls.addWidget(self.solid_bg_auto_btn, 0, Qt.AlignmentFlag.AlignRight)

        export_layout.addLayout(bg_controls)
        
        export_group.setLayout(export_layout)
        self.main_layout.addWidget(export_group)

        # Audio controls
        audio_group = QGroupBox("Audio")
        audio_layout = QVBoxLayout()

        self.audio_enable_checkbox = QCheckBox("Enable Audio")
        self.audio_enable_checkbox.setChecked(True)
        self.audio_enable_checkbox.toggled.connect(self.audio_enabled_changed.emit)
        audio_layout.addWidget(self.audio_enable_checkbox)

        volume_layout = QHBoxLayout()
        volume_layout.addWidget(QLabel("Volume:"))
        self.audio_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.audio_volume_slider.setRange(0, 100)
        self.audio_volume_slider.setValue(80)
        self.audio_volume_slider.valueChanged.connect(self.audio_volume_changed.emit)
        volume_layout.addWidget(self.audio_volume_slider)
        audio_layout.addLayout(volume_layout)

        self.audio_status_label = QLabel("Audio: not loaded")
        self.audio_status_label.setStyleSheet("color: gray; font-size: 8pt;")
        audio_layout.addWidget(self.audio_status_label)

        audio_group.setLayout(audio_layout)
        self.main_layout.addWidget(audio_group)

        presets_group = QGroupBox("Layer Offset Presets")
        presets_layout = QVBoxLayout()

        info_label = QLabel("Save/load sprite drag offsets for reuse")
        info_label.setStyleSheet("color: gray; font-size: 8pt;")
        presets_layout.addWidget(info_label)

        save_btn = QPushButton("Save Offsets...")
        save_btn.clicked.connect(self.save_offsets_clicked.emit)
        presets_layout.addWidget(save_btn)

        load_btn = QPushButton("Load Offsets...")
        load_btn.clicked.connect(self.load_offsets_clicked.emit)
        presets_layout.addWidget(load_btn)

        presets_group.setLayout(presets_layout)
        self.main_layout.addWidget(presets_group)

        diag_group = QGroupBox("Diagnostics")
        diag_layout = QVBoxLayout()

        self.diag_enable_checkbox = QCheckBox("Enable diagnostics logging")
        self.diag_enable_checkbox.setToolTip("Toggle the runtime diagnostics overlay/logging system.")
        self.diag_enable_checkbox.toggled.connect(self.diagnostics_enabled_changed.emit)
        diag_layout.addWidget(self.diag_enable_checkbox)

        self.diag_refresh_button = QPushButton("Refresh Layer Diagnostics")
        self.diag_refresh_button.clicked.connect(self.diagnostics_refresh_requested.emit)
        diag_layout.addWidget(self.diag_refresh_button)

        self.diag_export_button = QPushButton("Export Diagnostics Log")
        self.diag_export_button.clicked.connect(self.diagnostics_export_requested.emit)
        diag_layout.addWidget(self.diag_export_button)

        diag_hint = QLabel("Configure advanced logging in Settings → Diagnostics.")
        diag_hint.setStyleSheet("color: gray; font-size: 8pt;")
        diag_layout.addWidget(diag_hint)

        diag_group.setLayout(diag_layout)
        self.main_layout.addWidget(diag_group)
        
        # About section
        about_group = QGroupBox("About")
        about_layout = QVBoxLayout()
        
        credits_btn = QPushButton("Credits && Acknowledgments")
        credits_btn.clicked.connect(self.credits_clicked.emit)
        about_layout.addWidget(credits_btn)
        
        about_group.setLayout(about_layout)
        self.main_layout.addWidget(about_group)
        
        self.main_layout.addStretch()

        self._solid_bg_color = (0, 0, 0, 255)
        self._solid_bg_update_guard = False
        self.set_solid_bg_color(self._solid_bg_color)
        self._set_solid_bg_controls_enabled(False)

    def update_audio_status(self, message: str, success: bool = False):
        """Update the inline audio status label."""
        color = "#32a852" if success else ("#d64541" if message else "gray")
        text = message if message else "Audio: not available"
        if not text.lower().startswith("audio"):
            text = f"Audio: {text}"
        self.audio_status_label.setText(text)
        self.audio_status_label.setStyleSheet(f"font-size: 8pt; color: {color};")

        # Placement control signal wiring
        self.translation_spin.valueChanged.connect(self._on_translation_spin_changed)
        self.translation_slider.valueChanged.connect(self._on_translation_slider_changed)
        self.rotation_spin.valueChanged.connect(self._on_rotation_spin_changed)
        self.rotation_slider.valueChanged.connect(self._on_rotation_slider_changed)
        self.rotation_overlay_spin.valueChanged.connect(self._on_rotation_overlay_spin_changed)
        self.rotation_overlay_slider.valueChanged.connect(self._on_rotation_overlay_slider_changed)
        self.rotation_gizmo_checkbox.toggled.connect(self.rotation_gizmo_toggled.emit)
        self.bpm_spin.valueChanged.connect(self._on_bpm_spin_changed)
        self.bpm_slider.valueChanged.connect(self._on_bpm_slider_changed)
        self.sync_audio_checkbox.toggled.connect(self.sync_audio_to_bpm_toggled.emit)
        self.pitch_shift_checkbox.toggled.connect(self.pitch_shift_toggled.emit)
        self.reset_bpm_button.clicked.connect(self.bpm_reset_requested.emit)
        self.lock_bpm_button.clicked.connect(self.base_bpm_lock_requested.emit)
        self.anchor_precision_spin.valueChanged.connect(self._on_anchor_precision_spin_changed)
        self.anchor_precision_slider.valueChanged.connect(self._on_anchor_precision_slider_changed)
        self.anchor_bias_x_spin.valueChanged.connect(self._on_anchor_bias_x_spin_changed)
        self.anchor_bias_x_slider.valueChanged.connect(self._on_anchor_bias_x_slider_changed)
        self.anchor_bias_y_spin.valueChanged.connect(self._on_anchor_bias_y_spin_changed)
        self.anchor_bias_y_slider.valueChanged.connect(self._on_anchor_bias_y_slider_changed)
        self.local_pos_spin.valueChanged.connect(self._on_local_pos_spin_changed)
        self.local_pos_slider.valueChanged.connect(self._on_local_pos_slider_changed)
        self.parent_mix_spin.valueChanged.connect(self._on_parent_mix_spin_changed)
        self.parent_mix_slider.valueChanged.connect(self._on_parent_mix_slider_changed)
        self.rotation_bias_spin.valueChanged.connect(self._on_rotation_bias_spin_changed)
        self.rotation_bias_slider.valueChanged.connect(self._on_rotation_bias_slider_changed)
        self.scale_bias_x_spin.valueChanged.connect(self._on_scale_bias_x_spin_changed)
        self.scale_bias_x_slider.valueChanged.connect(self._on_scale_bias_x_slider_changed)
        self.scale_bias_y_spin.valueChanged.connect(self._on_scale_bias_y_spin_changed)
        self.scale_bias_y_slider.valueChanged.connect(self._on_scale_bias_y_slider_changed)
        self.world_offset_x_spin.valueChanged.connect(self._on_world_offset_x_spin_changed)
        self.world_offset_x_slider.valueChanged.connect(self._on_world_offset_x_slider_changed)
        self.world_offset_y_spin.valueChanged.connect(self._on_world_offset_y_spin_changed)
        self.world_offset_y_slider.valueChanged.connect(self._on_world_offset_y_slider_changed)
        self.trim_shift_spin.valueChanged.connect(self._on_trim_shift_spin_changed)
        self.trim_shift_slider.valueChanged.connect(self._on_trim_shift_slider_changed)

    def _make_channel_spinbox(self, name: str, handler):
        spin = QSpinBox()
        spin.setRange(0, 255)
        spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        spin.setFixedWidth(55)
        spin.valueChanged.connect(handler)
        spin.setToolTip(f"{name} channel (0-255)")
        return spin

    def _on_solid_bg_toggled(self, enabled: bool):
        self._set_solid_bg_controls_enabled(enabled)
        self.solid_bg_enabled_changed.emit(enabled)

    def _set_solid_bg_controls_enabled(self, enabled: bool):
        for widget in (
            self.solid_bg_color_btn,
            self.solid_bg_hex_input,
            self.solid_bg_r_spin,
            self.solid_bg_g_spin,
            self.solid_bg_b_spin,
            self.solid_bg_a_spin,
            self.solid_bg_auto_btn,
        ):
            widget.setEnabled(enabled)

    def _on_solid_bg_pick_color(self):
        from PyQt6.QtGui import QColor
        color = QColor(*self._solid_bg_color)
        selected = QColorDialog.getColor(
            color,
            self,
            "Select Background Color",
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if selected.isValid():
            self._set_solid_bg_color(
                (selected.red(), selected.green(), selected.blue(), selected.alpha()),
                emit=True,
            )

    def _on_solid_bg_hex_changed(self):
        if self._solid_bg_update_guard:
            return
        value = self.solid_bg_hex_input.text().strip().lstrip("#")
        if len(value) == 6:
            value += "FF"
        if len(value) != 8:
            self._set_solid_bg_color(self._solid_bg_color, emit=False)
            return
        try:
            r = int(value[0:2], 16)
            g = int(value[2:4], 16)
            b = int(value[4:6], 16)
            a = int(value[6:8], 16)
        except ValueError:
            self._set_solid_bg_color(self._solid_bg_color, emit=False)
            return
        self._set_solid_bg_color((r, g, b, a), emit=True)

    def _on_solid_bg_spin_changed(self, _value: int):
        if self._solid_bg_update_guard:
            return
        rgba = (
            int(self.solid_bg_r_spin.value()),
            int(self.solid_bg_g_spin.value()),
            int(self.solid_bg_b_spin.value()),
            int(self.solid_bg_a_spin.value()),
        )
        self._set_solid_bg_color(rgba, emit=True)

    def _set_solid_bg_color(self, rgba: Tuple[int, int, int, int], *, emit: bool):
        r = max(0, min(255, int(rgba[0])))
        g = max(0, min(255, int(rgba[1])))
        b = max(0, min(255, int(rgba[2])))
        a = max(0, min(255, int(rgba[3])))
        self._solid_bg_color = (r, g, b, a)
        self._solid_bg_update_guard = True
        self.solid_bg_r_spin.setValue(r)
        self.solid_bg_g_spin.setValue(g)
        self.solid_bg_b_spin.setValue(b)
        self.solid_bg_a_spin.setValue(a)
        hex_value = f"#{r:02X}{g:02X}{b:02X}{a:02X}"
        self.solid_bg_hex_input.setText(hex_value)
        self.solid_bg_color_btn.setStyleSheet(
            f"""
            QPushButton {{
                border: 1px solid #ccc;
                border-radius: 4px;
                padding: 6px;
                background-color: rgba({r}, {g}, {b}, {a});
            }}
            QPushButton:disabled {{
                background-color: palette(button);
                color: #999;
            }}
            """
        )
        self._solid_bg_update_guard = False
        if emit:
            self.solid_bg_color_changed.emit(r, g, b, a)

    def set_solid_bg_enabled(self, enabled: bool):
        self.solid_bg_checkbox.blockSignals(True)
        self.solid_bg_checkbox.setChecked(enabled)
        self.solid_bg_checkbox.blockSignals(False)
        self._set_solid_bg_controls_enabled(enabled)

    def set_solid_bg_color(self, rgba: Tuple[int, int, int, int]):
        self._set_solid_bg_color(rgba, emit=False)

    def reset_placement_bias_settings(self):
        """Reset all placement bias controls to their default values."""
        self.anchor_precision_spin.setValue(0.25)
        self.anchor_bias_x_spin.setValue(0.0)
        self.anchor_bias_y_spin.setValue(0.0)
        self.local_pos_spin.setValue(1.0)
        self.parent_mix_spin.setValue(1.0)
        self.rotation_bias_spin.setValue(0.0)
        self.scale_bias_x_spin.setValue(1.0)
        self.scale_bias_y_spin.setValue(1.0)
        self.world_offset_x_spin.setValue(0.0)
        self.world_offset_y_spin.setValue(0.0)
        self.trim_shift_spin.setValue(1.0)

    def set_bpm_value(self, value: float):
        """Synchronize BPM controls without emitting change signals."""
        clamped = max(20.0, min(300.0, value))
        slider_val = int(clamped * 10)
        self.bpm_spin.blockSignals(True)
        self.bpm_spin.setValue(clamped)
        self.bpm_spin.blockSignals(False)
        self.bpm_slider.blockSignals(True)
        self.bpm_slider.setValue(slider_val)
        self.bpm_slider.blockSignals(False)

    def set_sync_audio_checkbox(self, enabled: bool):
        """Set sync-audio toggle without feedback."""
        self.sync_audio_checkbox.blockSignals(True)
        self.sync_audio_checkbox.setChecked(enabled)
        self.sync_audio_checkbox.blockSignals(False)

    def set_pitch_shift_checkbox(self, enabled: bool):
        """Set pitch-shift toggle without feedback."""
        self.pitch_shift_checkbox.blockSignals(True)
        self.pitch_shift_checkbox.setChecked(enabled)
        self.pitch_shift_checkbox.blockSignals(False)

    def _on_translation_spin_changed(self, value: float):
        """Sync translation slider with spinbox and emit change."""
        self.translation_slider.blockSignals(True)
        self.translation_slider.setValue(int(value * 100))
        self.translation_slider.blockSignals(False)
        self.translation_sensitivity_changed.emit(value)

    def _on_translation_slider_changed(self, slider_value: int):
        """Sync translation spinbox with slider and emit change."""
        value = slider_value / 100.0
        self.translation_spin.blockSignals(True)
        self.translation_spin.setValue(value)
        self.translation_spin.blockSignals(False)
        self.translation_sensitivity_changed.emit(value)

    def _on_rotation_spin_changed(self, value: float):
        """Sync rotation slider with spinbox and emit change."""
        self.rotation_slider.blockSignals(True)
        self.rotation_slider.setValue(int(value * 10))
        self.rotation_slider.blockSignals(False)
        self.rotation_sensitivity_changed.emit(value)

    def _on_rotation_slider_changed(self, slider_value: int):
        """Sync rotation spinbox with slider and emit change."""
        value = slider_value / 10.0
        self.rotation_spin.blockSignals(True)
        self.rotation_spin.setValue(value)
        self.rotation_spin.blockSignals(False)
        self.rotation_sensitivity_changed.emit(value)

    def _on_rotation_overlay_spin_changed(self, value: float):
        """Sync overlay size slider with spinbox and emit change."""
        self.rotation_overlay_slider.blockSignals(True)
        self.rotation_overlay_slider.setValue(int(value))
        self.rotation_overlay_slider.blockSignals(False)
        self.rotation_overlay_size_changed.emit(value)

    def _on_rotation_overlay_slider_changed(self, slider_value: int):
        """Sync overlay size spinbox with slider and emit change."""
        value = float(slider_value)
        self.rotation_overlay_spin.blockSignals(True)
        self.rotation_overlay_spin.setValue(value)
        self.rotation_overlay_spin.blockSignals(False)
        self.rotation_overlay_size_changed.emit(value)

    def _on_bpm_spin_changed(self, value: float):
        """Sync BPM slider with spinbox and emit change."""
        slider_value = int(value * 10)
        self.bpm_slider.blockSignals(True)
        self.bpm_slider.setValue(slider_value)
        self.bpm_slider.blockSignals(False)
        self.bpm_value_changed.emit(value)

    def _on_bpm_slider_changed(self, slider_value: int):
        """Sync BPM spinbox with slider and emit change."""
        value = slider_value / 10.0
        self.bpm_spin.blockSignals(True)
        self.bpm_spin.setValue(value)
        self.bpm_spin.blockSignals(False)
        self.bpm_value_changed.emit(value)

    def _on_anchor_precision_spin_changed(self, value: float):
        """Sync anchor precision slider with spinbox and emit change."""
        self.anchor_precision_slider.blockSignals(True)
        self.anchor_precision_slider.setValue(int(value * 100))
        self.anchor_precision_slider.blockSignals(False)
        self.anchor_drag_precision_changed.emit(value)

    def _on_anchor_precision_slider_changed(self, slider_value: int):
        """Sync anchor precision spinbox with slider and emit change."""
        value = slider_value / 100.0
        self.anchor_precision_spin.blockSignals(True)
        self.anchor_precision_spin.setValue(value)
        self.anchor_precision_spin.blockSignals(False)
        self.anchor_drag_precision_changed.emit(value)

    def _on_anchor_bias_x_spin_changed(self, value: float):
        self.anchor_bias_x_slider.blockSignals(True)
        self.anchor_bias_x_slider.setValue(int(value * 100))
        self.anchor_bias_x_slider.blockSignals(False)
        self.anchor_bias_x_changed.emit(value)

    def _on_anchor_bias_x_slider_changed(self, slider_value: int):
        value = slider_value / 100.0
        self.anchor_bias_x_spin.blockSignals(True)
        self.anchor_bias_x_spin.setValue(value)
        self.anchor_bias_x_spin.blockSignals(False)
        self.anchor_bias_x_changed.emit(value)

    def _on_anchor_bias_y_spin_changed(self, value: float):
        self.anchor_bias_y_slider.blockSignals(True)
        self.anchor_bias_y_slider.setValue(int(value * 100))
        self.anchor_bias_y_slider.blockSignals(False)
        self.anchor_bias_y_changed.emit(value)

    def _on_anchor_bias_y_slider_changed(self, slider_value: int):
        value = slider_value / 100.0
        self.anchor_bias_y_spin.blockSignals(True)
        self.anchor_bias_y_spin.setValue(value)
        self.anchor_bias_y_spin.blockSignals(False)
        self.anchor_bias_y_changed.emit(value)

    def _on_local_pos_spin_changed(self, value: float):
        self.local_pos_slider.blockSignals(True)
        self.local_pos_slider.setValue(int(value * 100))
        self.local_pos_slider.blockSignals(False)
        self.local_position_multiplier_changed.emit(value)

    def _on_local_pos_slider_changed(self, slider_value: int):
        value = slider_value / 100.0
        self.local_pos_spin.blockSignals(True)
        self.local_pos_spin.setValue(value)
        self.local_pos_spin.blockSignals(False)
        self.local_position_multiplier_changed.emit(value)

    def _on_parent_mix_spin_changed(self, value: float):
        self.parent_mix_slider.blockSignals(True)
        self.parent_mix_slider.setValue(int(value * 100))
        self.parent_mix_slider.blockSignals(False)
        self.parent_mix_changed.emit(value)

    def _on_parent_mix_slider_changed(self, slider_value: int):
        value = slider_value / 100.0
        self.parent_mix_spin.blockSignals(True)
        self.parent_mix_spin.setValue(value)
        self.parent_mix_spin.blockSignals(False)
        self.parent_mix_changed.emit(value)

    def _on_rotation_bias_spin_changed(self, value: float):
        self.rotation_bias_slider.blockSignals(True)
        self.rotation_bias_slider.setValue(int(value * 10))
        self.rotation_bias_slider.blockSignals(False)
        self.rotation_bias_changed.emit(value)

    def _on_rotation_bias_slider_changed(self, slider_value: int):
        value = slider_value / 10.0
        self.rotation_bias_spin.blockSignals(True)
        self.rotation_bias_spin.setValue(value)
        self.rotation_bias_spin.blockSignals(False)
        self.rotation_bias_changed.emit(value)

    def _on_scale_bias_x_spin_changed(self, value: float):
        self.scale_bias_x_slider.blockSignals(True)
        self.scale_bias_x_slider.setValue(int(value * 100))
        self.scale_bias_x_slider.blockSignals(False)
        self.scale_bias_x_changed.emit(value)

    def _on_scale_bias_x_slider_changed(self, slider_value: int):
        value = slider_value / 100.0
        self.scale_bias_x_spin.blockSignals(True)
        self.scale_bias_x_spin.setValue(value)
        self.scale_bias_x_spin.blockSignals(False)
        self.scale_bias_x_changed.emit(value)

    def _on_scale_bias_y_spin_changed(self, value: float):
        self.scale_bias_y_slider.blockSignals(True)
        self.scale_bias_y_slider.setValue(int(value * 100))
        self.scale_bias_y_slider.blockSignals(False)
        self.scale_bias_y_changed.emit(value)

    def _on_scale_bias_y_slider_changed(self, slider_value: int):
        value = slider_value / 100.0
        self.scale_bias_y_spin.blockSignals(True)
        self.scale_bias_y_spin.setValue(value)
        self.scale_bias_y_spin.blockSignals(False)
        self.scale_bias_y_changed.emit(value)

    def _on_world_offset_x_spin_changed(self, value: float):
        self.world_offset_x_slider.blockSignals(True)
        self.world_offset_x_slider.setValue(int(value * 100))
        self.world_offset_x_slider.blockSignals(False)
        self.world_offset_x_changed.emit(value)

    def _on_world_offset_x_slider_changed(self, slider_value: int):
        value = slider_value / 100.0
        self.world_offset_x_spin.blockSignals(True)
        self.world_offset_x_spin.setValue(value)
        self.world_offset_x_spin.blockSignals(False)
        self.world_offset_x_changed.emit(value)

    def _on_world_offset_y_spin_changed(self, value: float):
        self.world_offset_y_slider.blockSignals(True)
        self.world_offset_y_slider.setValue(int(value * 100))
        self.world_offset_y_slider.blockSignals(False)
        self.world_offset_y_changed.emit(value)

    def _on_world_offset_y_slider_changed(self, slider_value: int):
        value = slider_value / 100.0
        self.world_offset_y_spin.blockSignals(True)
        self.world_offset_y_spin.setValue(value)
        self.world_offset_y_spin.blockSignals(False)
        self.world_offset_y_changed.emit(value)

    def _on_trim_shift_spin_changed(self, value: float):
        self.trim_shift_slider.blockSignals(True)
        self.trim_shift_slider.setValue(int(value * 100))
        self.trim_shift_slider.blockSignals(False)
        self.trim_shift_multiplier_changed.emit(value)

    def _on_trim_shift_slider_changed(self, slider_value: int):
        value = slider_value / 100.0
        self.trim_shift_spin.blockSignals(True)
        self.trim_shift_spin.setValue(value)
        self.trim_shift_spin.blockSignals(False)
        self.trim_shift_multiplier_changed.emit(value)

    def update_file_count_label(self, shown: int, total: int):
        """
        Update the label displaying how many files are visible
        
        Args:
            shown: Number of files currently shown in the dropdown
            total: Total number of indexed files
        """
        if total == 0:
            text = "No files indexed"
        elif shown == total:
            text = f"Showing {total} files"
        else:
            text = f"Showing {shown} of {total} files"
        self.file_count_label.setText(text)

    def set_barebones_file_mode(self, enabled: bool):
        """Toggle between classic list search and the monster browser shortcut."""
        if hasattr(self, "barebones_container"):
            self.barebones_container.setVisible(enabled)
        if hasattr(self, "monster_browser_container"):
            self.monster_browser_container.setVisible(not enabled)

    def update_costume_options(self, entries: List[Tuple[str, Any]], select_index: int = 0):
        """
        Populate the costume combo box.

        Args:
            entries: Sequence of (label, userData) tuples for available costumes.
            select_index: Desired combo index after repopulating (default selects base).
        """
        combo = self.costume_combo
        was_blocked = combo.blockSignals(True)
        combo.clear()
        combo.addItem("No Costume (Base)", None)
        for label, data in entries:
            combo.addItem(label, data)
        combo.blockSignals(was_blocked)

        combo.setEnabled(combo.count() > 1)
        if 0 <= select_index < combo.count():
            combo.setCurrentIndex(select_index)
        else:
            combo.setCurrentIndex(0)
        self.set_costume_convert_enabled(combo.currentData() is not None)

    def set_costume_convert_enabled(self, enabled: bool):
        """Enable/disable the costume conversion button."""
        self.costume_convert_btn.setEnabled(enabled)

    def set_diagnostics_enabled(self, enabled: bool):
        """Sync the diagnostics checkbox without emitting."""
        self.diag_enable_checkbox.blockSignals(True)
        self.diag_enable_checkbox.setChecked(enabled)
        self.diag_enable_checkbox.blockSignals(False)

    def set_pose_mode(self, mode: str):
        """Select a pose influence option."""
        if not hasattr(self, "pose_mode_combo"):
            return
        for idx in range(self.pose_mode_combo.count()):
            if self.pose_mode_combo.itemData(idx) == mode:
                self.pose_mode_combo.blockSignals(True)
                self.pose_mode_combo.setCurrentIndex(idx)
                self.pose_mode_combo.blockSignals(False)
                return

    def set_pose_controls_enabled(self, enabled: bool):
        """Enable/disable pose capture UI."""
        if hasattr(self, "record_pose_btn"):
            self.record_pose_btn.setEnabled(enabled)
        if hasattr(self, "pose_mode_combo"):
            self.pose_mode_combo.setEnabled(enabled)
        if hasattr(self, "reset_pose_btn"):
            self.reset_pose_btn.setEnabled(enabled)
        if hasattr(self, "undo_keyframe_btn"):
            self.undo_keyframe_btn.setEnabled(enabled)
        if hasattr(self, "redo_keyframe_btn"):
            self.redo_keyframe_btn.setEnabled(enabled)
        if hasattr(self, "delete_other_keyframes_btn"):
            self.delete_other_keyframes_btn.setEnabled(enabled)
        if hasattr(self, "extend_duration_btn"):
            self.extend_duration_btn.setEnabled(enabled)
        if hasattr(self, "save_animation_btn"):
            self.save_animation_btn.setEnabled(enabled)
        if hasattr(self, "load_animation_btn"):
            self.load_animation_btn.setEnabled(True)

    def set_sprite_tools_enabled(self, enabled: bool):
        """Enable or disable sprite variation controls."""
        if hasattr(self, "assign_sprite_btn"):
            self.assign_sprite_btn.setEnabled(enabled)

    def set_keyframe_history_state(self, undo_available: bool, redo_available: bool):
        """Enable/disable undo and redo buttons based on history state."""
        if hasattr(self, "undo_keyframe_btn"):
            self.undo_keyframe_btn.setEnabled(undo_available)
        if hasattr(self, "redo_keyframe_btn"):
            self.redo_keyframe_btn.setEnabled(redo_available)
    
    def update_offset_display(self, layer_offsets, get_layer_by_id_func, layer_rotations=None, layer_scales=None):
        """
        Update the offset display
        
        Args:
            layer_offsets: Dictionary of layer_id -> (offset_x, offset_y)
            get_layer_by_id_func: Function to get layer by ID
            layer_rotations: Optional dictionary of rotation offsets
            layer_scales: Optional dictionary of scale multipliers
        """
        # Clear existing labels
        while self.offset_display_layout.count() > 1:
            item = self.offset_display_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        rotation_map = layer_rotations or {}
        scale_map = layer_scales or {}
        layer_ids = set(layer_offsets.keys())
        layer_ids.update(rotation_map.keys())
        layer_ids.update(scale_map.keys())
        
        if not layer_ids:
            no_offsets_label = QLabel("No offsets applied")
            no_offsets_label.setStyleSheet("color: gray; font-style: italic;")
            self.offset_display_layout.insertWidget(0, no_offsets_label)
            return
        
        for layer_id in sorted(layer_ids):
            offset_x, offset_y = layer_offsets.get(layer_id, (0.0, 0.0))
            rotation_value = rotation_map.get(layer_id, 0.0)
            scale_value = scale_map.get(layer_id, (1.0, 1.0))
            layer = get_layer_by_id_func(layer_id)
            if layer:
                details = f"{layer.name}: ({offset_x:.2f}, {offset_y:.2f})"
                if abs(rotation_value) > 0.0001:
                    details += f"  Rot {rotation_value:.1f}°"
                if abs(scale_value[0] - 1.0) > 0.0001 or abs(scale_value[1] - 1.0) > 0.0001:
                    details += f"  Scale {scale_value[0]:.2f}/{scale_value[1]:.2f}"
                offset_label = QLabel(details)
                offset_label.setStyleSheet("font-size: 9pt;")
                self.offset_display_layout.insertWidget(
                    self.offset_display_layout.count() - 1, 
                    offset_label
                )

    # ------------------------------------------------------------------ #
    # Nudging helper methods
    # ------------------------------------------------------------------ #
    def _emit_nudge_x(self, direction: int):
        """Emit X position nudge signal with current step size."""
        step = self.nudge_step_spin.value() * direction
        self.nudge_x_changed.emit(step)

    def _emit_nudge_y(self, direction: int):
        """Emit Y position nudge signal with current step size."""
        step = self.nudge_step_spin.value() * direction
        self.nudge_y_changed.emit(step)

    def _emit_nudge_rotation(self, direction: int):
        """Emit rotation nudge signal with current rotation step."""
        step = self.nudge_rot_step_spin.value() * direction
        self.nudge_rotation_changed.emit(step)

    def _emit_nudge_scale_uniform(self, direction: int):
        """Emit uniform scale nudge (both X and Y)."""
        step = self.nudge_scale_step_spin.value() * direction
        self.nudge_scale_x_changed.emit(step)
        self.nudge_scale_y_changed.emit(step)

    def _emit_nudge_scale_x(self, direction: int):
        """Emit X scale nudge signal."""
        step = self.nudge_scale_step_spin.value() * direction
        self.nudge_scale_x_changed.emit(step)

    def _emit_nudge_scale_y(self, direction: int):
        """Emit Y scale nudge signal."""
        step = self.nudge_scale_step_spin.value() * direction
        self.nudge_scale_y_changed.emit(step)

    def set_nudge_controls_enabled(self, enabled: bool):
        """Enable or disable all nudging controls based on layer selection."""
        for widget in (
            self.nudge_step_spin,
            self.nudge_x_minus_btn,
            self.nudge_x_plus_btn,
            self.nudge_y_minus_btn,
            self.nudge_y_plus_btn,
            self.nudge_rot_step_spin,
            self.nudge_rot_minus_btn,
            self.nudge_rot_plus_btn,
            self.nudge_scale_step_spin,
            self.nudge_scale_minus_btn,
            self.nudge_scale_plus_btn,
            self.nudge_scale_x_minus_btn,
            self.nudge_scale_x_plus_btn,
            self.nudge_scale_y_minus_btn,
            self.nudge_scale_y_plus_btn,
        ):
            widget.setEnabled(enabled)
