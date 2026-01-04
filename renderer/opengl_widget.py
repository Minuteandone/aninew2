"""
OpenGL Animation Widget
Qt widget that handles OpenGL rendering, camera controls, and user interaction
"""

import os
os.environ.setdefault('QT_OPENGL', 'desktop')

import time
import math
from dataclasses import dataclass
from typing import Any, List, Dict, Tuple, Optional, Set

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QSurfaceFormat
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from OpenGL.GL import *
from OpenGL.GLU import *

from core.data_structures import AnimationData, LayerData
from core.animation_player import AnimationPlayer
from core.texture_atlas import TextureAtlas
from .sprite_renderer import SpriteRenderer
from utils.shader_registry import ShaderRegistry


@dataclass
class AttachmentInstance:
    """Runtime state for an attached animation."""
    name: str
    target_layer: str
    target_layer_id: Optional[int]
    player: AnimationPlayer
    atlases: List[TextureAtlas]
    time_offset: float
    tempo_multiplier: float = 1.0
    loop: bool = True
    root_layer_name: Optional[str] = None
    allow_base_fallback: bool = False


class OpenGLAnimationWidget(QOpenGLWidget):
    """
    OpenGL widget for rendering animations
    Handles rendering, camera controls, and user interaction
    """

    animation_time_changed = pyqtSignal(float, float)
    animation_looped = pyqtSignal()
    playback_state_changed = pyqtSignal(bool)
    transform_action_committed = pyqtSignal(dict)
    
    def __init__(self, parent: Optional[QWidget] = None, shader_registry: Optional[ShaderRegistry] = None):
        super().__init__(parent)
        
        # Core components
        self.texture_atlases: List[TextureAtlas] = []
        self.player = AnimationPlayer()
        # Global tweening flag for this widget (controls whether linear interpolation is used)
        self.tweening_enabled: bool = True
        self.player.tweening_enabled = True
        self.renderer = SpriteRenderer()
        self.renderer.set_shader_registry(shader_registry)
        self.renderer.set_costume_pivot_adjustment_enabled(False)
        self.antialias_enabled: bool = True
        self.zoom_to_cursor: bool = True
        self.attachment_instances: List[AttachmentInstance] = []
        self.layer_atlas_overrides: Dict[int, List[TextureAtlas]] = {}
        self.layer_pivot_context: Dict[int, bool] = {}
        
        # Rendering settings
        self.render_scale: float = 1.0
        self.background_color = (0.2, 0.2, 0.2, 1.0)
        self.show_bones: bool = False
        
        # Camera controls
        self.camera_x: float = 0.0
        self.camera_y: float = 0.0
        self.dragging_camera: bool = False
        self.last_mouse_x: int = 0
        self.last_mouse_y: int = 0
        
        # Sprite dragging / selection
        self.dragging_sprite: bool = False
        self.selected_layer_id: Optional[int] = None  # Primary selection
        self.selected_layer_ids: Set[int] = set()
        self.selection_group_lock: bool = False
        self.dragged_layer_id: Optional[int] = None
        self.layer_offsets: Dict[int, Tuple[float, float]] = {}
        self.layer_rotations: Dict[int, float] = {}
        self.layer_scale_offsets: Dict[int, Tuple[float, float]] = {}
        self.drag_translation_multiplier: float = 1.0
        self.drag_rotation_multiplier: float = 1.0
        self.rotation_gizmo_enabled: bool = False
        self.rotation_overlay_radius: float = 120.0
        self.rotation_dragging: bool = False
        self.rotation_drag_last_angle: float = 0.0
        self.rotation_drag_accum: float = 0.0
        self.rotation_initial_values: Dict[int, float] = {}
        self.scale_gizmo_enabled: bool = False
        self.scale_mode: str = "Uniform"
        self.scale_dragging: bool = False
        self.scale_drag_axis: str = "uniform"
        self.scale_drag_initials: Dict[int, Tuple[float, float]] = {}
        self.scale_drag_start: float = 0.0
        self.scale_drag_center: Tuple[float, float] = (0.0, 0.0)
        self._scale_handle_positions: Dict[str, Tuple[float, float]] = {}
        self._last_layer_world_states: Dict[int, Dict] = {}
        self.anchor_overlay_enabled: bool = False
        self.parent_overlay_enabled: bool = False
        self.layer_anchor_overrides: Dict[int, Tuple[float, float]] = {}
        self.renderer.anchor_overrides = self.layer_anchor_overrides
        self._anchor_handle_positions: Dict[int, Tuple[float, float]] = {}
        self._parent_handle_positions: Dict[int, Tuple[float, float]] = {}
        self._anchor_hover_layer_id: Optional[int] = None
        self.anchor_dragging: bool = False
        self.anchor_drag_layer_id: Optional[int] = None
        self.parent_dragging: bool = False
        self.parent_drag_layer_id: Optional[int] = None
        self.anchor_drag_last_world: Tuple[float, float] = (0.0, 0.0)
        self.parent_drag_last_world: Tuple[float, float] = (0.0, 0.0)
        self.anchor_drag_precision: float = 0.25
        self._layer_order_map: Dict[int, int] = {}
        self._active_transform_ids: List[int] = []
        self._active_transform_snapshot: Optional[Dict] = None
        self._current_drag_targets: List[int] = []
        
        # Timing
        self.last_update_time: Optional[float] = None
        
        # Set OpenGL format
        fmt = QSurfaceFormat()
        fmt.setVersion(2, 1)
        # Request a compatibility profile so legacy OpenGL calls (glBegin/glEnd) work,
        # especially on macOS where CoreProfile contexts forbid fixed-function APIs.
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.NoProfile)
        fmt.setSamples(4)
        self.setFormat(fmt)
        
        # Timer for animation updates
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(16)  # ~60 FPS

        # Glitch options (widget-level defaults propagated to players)
        self.glitch_jitter_enabled: bool = False
        self.jitter_amplitude: float = 1.0
        self.glitch_sprite_enabled: bool = False
        self.glitch_sprite_chance: float = 0.1

        # Anchor logging is controlled by the main window preferences
        self.renderer.enable_logging = False
        
        # Enable mouse tracking
        self.setMouseTracking(True)
        
        # Enable keyboard focus
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    
    @property
    def position_scale(self) -> float:
        """Get position scale from renderer"""
        return self.renderer.position_scale
    
    @position_scale.setter
    def position_scale(self, value: float):
        """Set position scale in renderer"""
        self.renderer.position_scale = value
    
    def initializeGL(self):
        """Initialize OpenGL"""
        glEnable(GL_BLEND)
        # Use premultiplied alpha blending like the MSM game engine
        # The game uses glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
        # This expects textures with premultiplied alpha (RGB * A)
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
        glEnable(GL_TEXTURE_2D)
        
        # Load textures
        for atlas in self.texture_atlases:
            atlas.load_texture()
        
        self._apply_antialiasing_state()
    
    def resizeGL(self, w: int, h: int):
        """Handle resize"""
        glViewport(0, 0, w, h)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        # Use Y-down coordinates like Pygame (origin at top-left)
        # This matches the JSON data format
        glOrtho(0, w, h, 0, -1, 1)
        glMatrixMode(GL_MODELVIEW)
    
    def paintGL(self):
        """Render the animation"""
        self._apply_antialiasing_state()
        glClearColor(*self.background_color)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        
        if not self.player.animation:
            return
        
        glLoadIdentity()
        
        # Apply camera offset
        glTranslatef(self.camera_x, self.camera_y, 0)
        glScalef(self.render_scale, self.render_scale, 1.0)
        
        # If animation is centered, translate to center of viewport
        # This makes (0,0) in animation space appear at the center of the screen
        if self.player.animation and self.player.animation.centered:
            w = self.width()
            h = self.height()
            glTranslatef(w / 2, h / 2, 0)
        
        # Render all layers respecting parent-child hierarchy
        layer_world_states = self.render_all_layers(self.player.current_time)
        
        # Render selection outlines for selected layers
        if self.selected_layer_ids:
            self.render_selection_outlines(layer_world_states)
        
        if self.anchor_overlay_enabled or self.parent_overlay_enabled:
            self.render_anchor_parent_overlay(layer_world_states)
        
        if self.rotation_gizmo_enabled:
            self.render_rotation_gizmo(layer_world_states)
        
        if self.scale_gizmo_enabled:
            self.render_scale_gizmo(layer_world_states)
        
        # Render bone overlay if enabled
        if self.show_bones:
            self.render_bone_overlay(self.player.current_time)
    
    def render_all_layers(self, time: float):
        """
        Render all layers in correct order with hierarchy
        
        Args:
            time: Current animation time
        """
        self.renderer.current_time = time
        self.renderer.animation_duration = self.player.duration or 0.0
        # Calculate world positions for all layers first
        layer_world_states = self._build_layer_world_states(time)
        self._last_layer_world_states = layer_world_states
        attachment_map = self._group_attachments_by_layer()
        self.renderer.reset_layer_masks()
        
        # Render all layers in REVERSE order (back to front)
        # In most animation systems, layers are listed from back to front,
        # so we need to render them in reverse to get the correct Z-order
        for layer in reversed(self.player.animation.layers):
            if layer.visible:
                world_state = layer_world_states[layer.layer_id]
                override_atlases = self.layer_atlas_overrides.get(layer.layer_id)
                atlas_chain = (
                    list(override_atlases) + self.texture_atlases
                    if override_atlases
                    else self.texture_atlases
                )
                self.renderer.render_layer(
                    layer, world_state, atlas_chain, self.layer_offsets
                )
                if attachment_map:
                    for instance in attachment_map.get(layer.layer_id, []):
                        self._render_attachment_layers(instance, world_state)
        
        return layer_world_states

    def _build_layer_world_states(self, anim_time: Optional[float] = None) -> Dict[int, Dict]:
        """
        Calculate world states for all layers, applying user rotation offsets.
        """
        if not self.player.animation:
            return {}

        if anim_time is None:
            anim_time = self.player.current_time

        layer_map = {layer.layer_id: layer for layer in self.player.animation.layers}
        self._layer_order_map = {
            layer.layer_id: idx for idx, layer in enumerate(self.player.animation.layers)
        }
        layer_world_states: Dict[int, Dict] = {}

        for layer in self.player.animation.layers:
            state = self.renderer.calculate_world_state(
                layer,
                anim_time,
                self.player,
                layer_map,
                layer_world_states,
                self.texture_atlases,
                self.layer_atlas_overrides,
                self.layer_pivot_context
            )
            layer_world_states[layer.layer_id] = self.apply_user_transforms(layer.layer_id, state)

        self._last_layer_world_states = layer_world_states
        return layer_world_states

    def _group_attachments_by_layer(self) -> Dict[int, List[AttachmentInstance]]:
        """Return attachment instances grouped by their target layer id."""
        grouping: Dict[int, List[AttachmentInstance]] = {}
        for instance in self.attachment_instances:
            if instance.target_layer_id is None:
                continue
            grouping.setdefault(instance.target_layer_id, []).append(instance)
        return grouping

    def _get_attachment_root_anchor(
        self,
        instance: AttachmentInstance,
        animation: Optional[AnimationData],
        world_states: Dict[int, Dict[str, float]]
    ) -> Tuple[float, float]:
        """
        Return the anchor in attachment space that should align with the parent anchor.
        """
        if not animation or not world_states:
            return (0.0, 0.0)

        preferred = (instance.root_layer_name or "").lower()
        if preferred:
            for layer in animation.layers:
                if layer.name.lower() == preferred:
                    state = world_states.get(layer.layer_id)
                    if state:
                        ax = state.get('anchor_world_x', state.get('tx', 0.0))
                        ay = state.get('anchor_world_y', state.get('ty', 0.0))
                        return (ax, ay)

        # Prefer explicit root layers (parent_id < 0). These define the authored pivot.
        for layer in animation.layers:
            if layer.parent_id >= 0:
                continue
            state = world_states.get(layer.layer_id)
            if not state:
                continue
            ax = state.get('anchor_world_x', state.get('tx', 0.0))
            ay = state.get('anchor_world_y', state.get('ty', 0.0))
            return (ax, ay)

        # Fallback to the first available layer if no explicit root could be resolved.
        sample_state = next(iter(world_states.values()), None)
        if not sample_state:
            return (0.0, 0.0)
        return (
            sample_state.get('anchor_world_x', sample_state.get('tx', 0.0)),
            sample_state.get('anchor_world_y', sample_state.get('ty', 0.0))
        )

    def _render_attachment_layers(
        self,
        instance: AttachmentInstance,
        parent_state: Dict[str, float]
    ) -> None:
        """Render a single attachment animation relative to its parent layer."""
        player = instance.player
        animation = player.animation
        if not animation:
            return
        player.current_time = self._compute_attachment_time(instance)
        previous_time = self.renderer.current_time
        self.renderer.current_time = player.current_time
        layer_map = {layer.layer_id: layer for layer in animation.layers}
        world_states: Dict[int, Dict] = {}
        atlas_chain = list(instance.atlases)
        if instance.allow_base_fallback:
            atlas_chain += self.texture_atlases
        for layer in animation.layers:
            state = self.renderer.calculate_world_state(
                layer,
                player.current_time,
                player,
                layer_map,
                world_states,
                atlas_chain
            )
            world_states[layer.layer_id] = state
        root_anchor = self._get_attachment_root_anchor(instance, animation, world_states)
        combined_states = {
            layer_id: self._combine_attachment_transform(parent_state, state, root_anchor)
            for layer_id, state in world_states.items()
        }
        for layer in reversed(animation.layers):
            if not layer.visible:
                continue
            state = combined_states.get(layer.layer_id)
            if not state:
                continue
            self.renderer.render_layer(layer, state, atlas_chain, {})
        self.renderer.current_time = previous_time

    def _compute_attachment_time(self, instance: AttachmentInstance) -> float:
        """Derive attachment playback time from the master animation clock."""
        if not self.player.animation:
            return 0.0
        animation = instance.player.animation
        if not animation:
            return 0.0
        master_time = self.player.current_time
        speed = max(0.1, float(instance.tempo_multiplier or 1.0))
        local_time = master_time * speed + instance.time_offset
        duration = instance.player.duration or 0.0
        if duration > 0:
            if instance.player.loop:
                local_time = math.fmod(local_time, duration)
                if local_time < 0:
                    local_time += duration
            else:
                local_time = max(0.0, min(local_time, duration))
        return max(0.0, local_time)

    def _combine_attachment_transform(
        self,
        parent_state: Dict[str, float],
        child_state: Dict[str, float],
        root_anchor: Tuple[float, float]
    ) -> Dict[str, float]:
        """Return a child transform composed with its parent's world matrix."""
        result = dict(child_state)
        pm00 = parent_state['m00']
        pm01 = parent_state['m01']
        pm10 = parent_state['m10']
        pm11 = parent_state['m11']
        ptx = parent_state.get('anchor_world_x', parent_state['tx'])
        pty = parent_state.get('anchor_world_y', parent_state['ty'])

        cm00 = child_state['m00']
        cm01 = child_state['m01']
        cm10 = child_state['m10']
        cm11 = child_state['m11']
        root_x, root_y = root_anchor
        ctx = child_state['tx'] - root_x
        cty = child_state['ty'] - root_y

        result['m00'] = pm00 * cm00 + pm01 * cm10
        result['m01'] = pm00 * cm01 + pm01 * cm11
        result['m10'] = pm10 * cm00 + pm11 * cm10
        result['m11'] = pm10 * cm01 + pm11 * cm11
        result['tx'] = pm00 * ctx + pm01 * cty + ptx
        result['ty'] = pm10 * ctx + pm11 * cty + pty

        anchor_x = child_state.get('anchor_world_x', child_state['tx']) - root_x
        anchor_y = child_state.get('anchor_world_y', child_state['ty']) - root_y
        result['anchor_world_x'] = pm00 * anchor_x + pm01 * anchor_y + ptx
        result['anchor_world_y'] = pm10 * anchor_x + pm11 * anchor_y + pty
        return result

    def apply_user_transforms(self, layer_id: int, state: Dict) -> Dict:
        """Apply user-driven rotation and scaling offsets to a layer."""
        rotation = self.layer_rotations.get(layer_id, 0.0)
        scale_x, scale_y = self.layer_scale_offsets.get(layer_id, (1.0, 1.0))

        needs_scale = (abs(scale_x - 1.0) > 1e-6) or (abs(scale_y - 1.0) > 1e-6)
        needs_rotation = abs(rotation) > 1e-6

        # Preserve original matrix components for multiplication
        base_m00 = state['m00']
        base_m01 = state['m01']
        base_m10 = state['m10']
        base_m11 = state['m11']
        base_tx = state['tx']
        base_ty = state['ty']

        if needs_scale or needs_rotation:
            # Build the user transform (scale -> rotate) around the layer's world pivot.
            rot_rad = math.radians(rotation)
            cos_r = math.cos(rot_rad)
            sin_r = math.sin(rot_rad)

            user_m00 = cos_r * scale_x
            user_m01 = -sin_r * scale_y
            user_m10 = sin_r * scale_x
            user_m11 = cos_r * scale_y

            center = self._get_layer_center_from_state(state, layer_id)
            offset_x, offset_y = self.layer_offsets.get(layer_id, (0.0, 0.0))
            if center:
                pivot_x = center[0] - offset_x
                pivot_y = center[1] - offset_y
            else:
                pivot_x = base_tx
                pivot_y = base_ty

            user_tx = pivot_x - (user_m00 * pivot_x + user_m01 * pivot_y)
            user_ty = pivot_y - (user_m10 * pivot_x + user_m11 * pivot_y)

            # Left-multiply the existing affine matrix with the user transform matrix.
            m00 = user_m00 * base_m00 + user_m01 * base_m10
            m01 = user_m00 * base_m01 + user_m01 * base_m11
            m10 = user_m10 * base_m00 + user_m11 * base_m10
            m11 = user_m10 * base_m01 + user_m11 * base_m11
            tx = user_m00 * base_tx + user_m01 * base_ty + user_tx
            ty = user_m10 * base_tx + user_m11 * base_ty + user_ty
        else:
            m00 = base_m00
            m01 = base_m01
            m10 = base_m10
            m11 = base_m11
            tx = base_tx
            ty = base_ty

        state['m00'] = m00
        state['m01'] = m01
        state['m10'] = m10
        state['m11'] = m11
        state['tx'] = tx
        state['ty'] = ty
        state['user_rotation'] = rotation
        state['user_scale'] = (scale_x, scale_y)
        return state

    def _get_anchor_world_position(self, state: Dict, layer_id: int) -> Tuple[float, float]:
        """Return world-space anchor for a layer including user offsets."""
        anchor_x = state.get('anchor_world_x', state['tx'])
        anchor_y = state.get('anchor_world_y', state['ty'])
        offset_x, offset_y = self.layer_offsets.get(layer_id, (0.0, 0.0))
        return anchor_x + offset_x, anchor_y + offset_y

    def _get_layer_center_from_state(self, state: Dict, layer_id: int) -> Tuple[float, float]:
        """Return world-space center for a layer including user offsets."""
        return self._get_anchor_world_position(state, layer_id)

    def get_layer_center(self, layer_id: Optional[int]) -> Optional[Tuple[float, float]]:
        """Get cached center position for a layer."""
        if layer_id is None or not self.player.animation:
            return None
        state = self._last_layer_world_states.get(layer_id)
        if state is None:
            states = self._build_layer_world_states()
            state = states.get(layer_id)
            if state:
                self._last_layer_world_states = states
        if not state:
            return None
        return self._get_layer_center_from_state(state, layer_id)

    def render_rotation_gizmo(self, layer_world_states: Dict[int, Dict]):
        """Draw the rotation overlay for the selected layer."""
        if not self.rotation_gizmo_enabled or self.selected_layer_id is None:
            return
        state = layer_world_states.get(self.selected_layer_id)
        if not state:
            return
        center = self._get_layer_center_from_state(state, self.selected_layer_id)
        cx, cy = center
        radius = max(5.0, self.rotation_overlay_radius)
        segments = 48

        glDisable(GL_TEXTURE_2D)
        glLineWidth(2.0)
        glColor4f(0.1, 0.9, 1.0, 0.85)
        glBegin(GL_LINE_LOOP)
        for i in range(segments):
            angle = 2 * math.pi * i / segments
            glVertex2f(cx + math.cos(angle) * radius, cy + math.sin(angle) * radius)
        glEnd()

        # Draw rotation handle showing current offset
        current_angle = math.radians(self.layer_rotations.get(self.selected_layer_id, 0.0))
        handle_x = cx + math.cos(current_angle) * radius
        handle_y = cy + math.sin(current_angle) * radius
        glBegin(GL_LINES)
        glVertex2f(cx, cy)
        glVertex2f(handle_x, handle_y)
        glEnd()
        glPointSize(6.0)
        glBegin(GL_POINTS)
        glVertex2f(handle_x, handle_y)
        glEnd()
        glPointSize(1.0)
        glLineWidth(1.0)
        glEnable(GL_TEXTURE_2D)

    def render_selection_outlines(self, layer_world_states: Dict[int, Dict]):
        """Draw green outlines around selected layer sprites."""
        if not self.selected_layer_ids or not self.player.animation:
            return
        
        glDisable(GL_TEXTURE_2D)
        glLineWidth(2.5)
        # Bright green color for selection outline
        glColor4f(0.2, 0.85, 0.4, 0.9)
        
        for layer in self.player.animation.layers:
            if layer.layer_id not in self.selected_layer_ids:
                continue
            if not layer.visible:
                continue
            
            world_state = layer_world_states.get(layer.layer_id)
            if not world_state:
                continue
            
            sprite_name = world_state.get('sprite_name', '')
            if not sprite_name:
                continue
            
            # Find sprite in atlases
            sprite = None
            atlas = None
            override_atlases = self.layer_atlas_overrides.get(layer.layer_id)
            atlas_chain = (
                list(override_atlases) + self.texture_atlases
                if override_atlases
                else self.texture_atlases
            )
            for atl in atlas_chain:
                sprite = atl.get_sprite(sprite_name)
                if sprite:
                    atlas = atl
                    break
            
            if not sprite or not atlas:
                continue
            
            # Get local vertices
            corners_local = self.renderer.compute_local_vertices(sprite, atlas)
            if not corners_local or len(corners_local) < 4:
                continue
            
            # Transform corners to world space
            m00 = world_state['m00']
            m01 = world_state['m01']
            m10 = world_state['m10']
            m11 = world_state['m11']
            tx = world_state['tx']
            ty = world_state['ty']
            
            # Apply user offset
            user_offset_x, user_offset_y = self.layer_offsets.get(layer.layer_id, (0, 0))
            
            world_corners = []
            for lx, ly in corners_local:
                wx = m00 * lx + m01 * ly + tx + user_offset_x
                wy = m10 * lx + m11 * ly + ty + user_offset_y
                world_corners.append((wx, wy))
            
            # Draw outline as a line loop
            glBegin(GL_LINE_LOOP)
            for wx, wy in world_corners:
                glVertex2f(wx, wy)
            glEnd()
        
        glLineWidth(1.0)
        glEnable(GL_TEXTURE_2D)

    def render_scale_gizmo(self, layer_world_states: Dict[int, Dict]):
        """Draw scale handles for the selected layer."""
        if not self.scale_gizmo_enabled or self.selected_layer_id is None:
            return
        state = layer_world_states.get(self.selected_layer_id)
        if not state:
            return
        center = self._get_layer_center_from_state(state, self.selected_layer_id)
        if not center:
            return
        cx, cy = center
        handle_len = max(60.0 / self.render_scale, 25.0)
        self._scale_handle_positions.clear()

        glDisable(GL_TEXTURE_2D)
        glLineWidth(2.0)

        if self.scale_mode.lower().startswith("uniform"):
            self._scale_handle_positions['uniform'] = (cx, cy, handle_len)
            glColor4f(0.2, 0.85, 0.4, 0.85)
            segments = 48
            glBegin(GL_LINE_LOOP)
            for i in range(segments):
                angle = 2 * math.pi * i / segments
                glVertex2f(cx + math.cos(angle) * handle_len, cy + math.sin(angle) * handle_len)
            glEnd()
        else:
            self._scale_handle_positions['x'] = (cx + handle_len, cy)
            self._scale_handle_positions['y'] = (cx, cy - handle_len)
            glColor4f(0.85, 0.8, 0.2, 0.9)
            glBegin(GL_LINES)
            glVertex2f(cx - handle_len, cy)
            glVertex2f(cx + handle_len, cy)
            glVertex2f(cx, cy - handle_len)
            glVertex2f(cx, cy + handle_len)
            glEnd()

            square = handle_len * 0.2
            glBegin(GL_QUADS)
            # X handle
            hx, hy = self._scale_handle_positions['x']
            glVertex2f(hx - square, hy - square)
            glVertex2f(hx + square, hy - square)
            glVertex2f(hx + square, hy + square)
            glVertex2f(hx - square, hy + square)
            # Y handle
            hx, hy = self._scale_handle_positions['y']
            glVertex2f(hx - square, hy - square)
            glVertex2f(hx + square, hy - square)
            glVertex2f(hx + square, hy + square)
            glVertex2f(hx - square, hy + square)
            glEnd()

        glLineWidth(1.0)
        glEnable(GL_TEXTURE_2D)
    
    def render_anchor_parent_overlay(self, layer_world_states: Dict[int, Dict]):
        """Draw anchor/parent overlays with draggable handles."""
        if not self.player.animation:
            return
        if not (self.anchor_overlay_enabled or self.parent_overlay_enabled):
            return
        
        anchor_radius = max(6.0 / max(self.render_scale, 1e-3), 4.0)
        parent_half = anchor_radius * 1.4
        self._anchor_handle_positions.clear()
        self._parent_handle_positions.clear()
        
        children_map: Dict[int, List[int]] = {}
        if self.parent_overlay_enabled:
            for layer in self.player.animation.layers:
                if layer.parent_id >= 0:
                    children_map.setdefault(layer.parent_id, []).append(layer.layer_id)
        
        glDisable(GL_TEXTURE_2D)
        glLineWidth(1.5)
        
        for layer in self.player.animation.layers:
            if not layer.visible:
                continue
            state = layer_world_states.get(layer.layer_id)
            if not state:
                continue
            ax, ay = self._get_anchor_world_position(state, layer.layer_id)
            
            if self.parent_overlay_enabled and layer.layer_id in children_map:
                self._parent_handle_positions[layer.layer_id] = (ax, ay)
                glColor4f(0.2, 0.7, 1.0, 0.85)
                for child_id in children_map[layer.layer_id]:
                    child_state = layer_world_states.get(child_id)
                    if not child_state:
                        continue
                    cx, cy = self._get_anchor_world_position(child_state, child_id)
                    glBegin(GL_LINES)
                    glVertex2f(ax, ay)
                    glVertex2f(cx, cy)
                    glEnd()
                glBegin(GL_LINE_LOOP)
                glVertex2f(ax - parent_half, ay - parent_half)
                glVertex2f(ax + parent_half, ay - parent_half)
                glVertex2f(ax + parent_half, ay + parent_half)
                glVertex2f(ax - parent_half, ay + parent_half)
                glEnd()
            
            if self.anchor_overlay_enabled:
                self._anchor_handle_positions[layer.layer_id] = (ax, ay)
                is_hovered = layer.layer_id == self._anchor_hover_layer_id
                if is_hovered:
                    glColor4f(1.0, 1.0, 1.0, 0.95)
                else:
                    glColor4f(0.95, 0.6, 0.2, 0.9)
                glBegin(GL_LINES)
                glVertex2f(ax - anchor_radius, ay)
                glVertex2f(ax + anchor_radius, ay)
                glVertex2f(ax, ay - anchor_radius)
                glVertex2f(ax, ay + anchor_radius)
                glEnd()
                glBegin(GL_LINE_LOOP)
                glVertex2f(ax - anchor_radius * 0.6, ay)
                glVertex2f(ax, ay + anchor_radius * 0.6)
                glVertex2f(ax + anchor_radius * 0.6, ay)
                glVertex2f(ax, ay - anchor_radius * 0.6)
                glEnd()
        
        glLineWidth(1.0)
        glEnable(GL_TEXTURE_2D)

    def _is_point_on_rotation_handle(self, world_x: float, world_y: float) -> bool:
        """Check if a point lies on the rotation gizmo ring."""
        if not self.rotation_gizmo_enabled or self.selected_layer_id is None:
            return False
        center = self.get_layer_center(self.selected_layer_id)
        if not center:
            return False
        cx, cy = center
        radius = max(5.0, self.rotation_overlay_radius)
        dx = world_x - cx
        dy = world_y - cy
        distance = math.hypot(dx, dy)
        tolerance = max(6.0, radius * 0.15)
        return abs(distance - radius) <= tolerance

    def _scale_handle_hit(self, world_x: float, world_y: float) -> Optional[str]:
        """Return which scale handle (if any) was hit."""
        if not self.scale_gizmo_enabled or self.selected_layer_id is None:
            return None
        mode = self.scale_mode.lower()
        if mode.startswith("uniform"):
            info = self._scale_handle_positions.get('uniform')
            if not info:
                return None
            cx, cy, radius = info
            distance = math.hypot(world_x - cx, world_y - cy)
            tolerance = max(10.0 / self.render_scale, 6.0)
            if abs(distance - radius) <= tolerance:
                return 'uniform'
        else:
            for axis in ('x', 'y'):
                if axis not in self._scale_handle_positions:
                    continue
                hx, hy = self._scale_handle_positions[axis]
                size = max(12.0 / self.render_scale, 6.0)
                if abs(world_x - hx) <= size and abs(world_y - hy) <= size:
                    return axis
        return None
    
    def _hit_anchor_handle(self, world_x: float, world_y: float) -> Optional[int]:
        """Return the layer id of the anchor handle hit, if any."""
        if not self.anchor_overlay_enabled:
            return None
        tolerance = max(10.0 / max(self.render_scale, 1e-3), 6.0)
        candidates: List[Tuple[int, float]] = []
        for layer_id, (hx, hy) in self._anchor_handle_positions.items():
            dist = math.hypot(world_x - hx, world_y - hy)
            if dist <= tolerance:
                candidates.append((layer_id, dist))
        if not candidates:
            return None

        primary_id = self.selected_layer_id
        selected_ids = self.selected_layer_ids or set()
        order_map = self._layer_order_map

        def sort_key(item: Tuple[int, float]):
            layer_id, dist = item
            primary_rank = 0 if primary_id is not None and layer_id == primary_id else 1
            selection_rank = 0 if layer_id in selected_ids else 1
            order_rank = order_map.get(layer_id, 0)
            return (primary_rank, selection_rank, dist, order_rank)

        candidates.sort(key=sort_key)
        return candidates[0][0]
    
    def _hit_parent_handle(self, world_x: float, world_y: float) -> Optional[int]:
        """Return the parent handle layer id if the point overlaps one."""
        if not self.parent_overlay_enabled:
            return None
        half = max(12.0 / max(self.render_scale, 1e-3), 7.0)
        for layer_id, (hx, hy) in self._parent_handle_positions.items():
            if abs(world_x - hx) <= half and abs(world_y - hy) <= half:
                return layer_id
        return None

    def _begin_scale_drag(self, axis: str, world_x: float, world_y: float):
        """Start interactive scaling for the selected layer."""
        layer_id = self.selected_layer_id
        if layer_id is None:
            return
        targets = self._get_drag_targets(layer_id)
        if not targets:
            return
        self.scale_dragging = True
        self.scale_drag_axis = axis
        self._current_drag_targets = targets
        self.scale_drag_initials = {
            target_id: self.layer_scale_offsets.get(target_id, (1.0, 1.0))
            for target_id in targets
        }
        center = self.get_layer_center(layer_id)
        self.scale_drag_center = center if center else (world_x, world_y)
        cx, cy = self.scale_drag_center
        if axis == 'uniform':
            self.scale_drag_start = max(math.hypot(world_x - cx, world_y - cy), 0.001)
        elif axis == 'x':
            self.scale_drag_start = max(abs(world_x - cx), 0.001)
        else:
            self.scale_drag_start = max(abs(world_y - cy), 0.001)
        self._begin_transform_action(targets)

    def _update_scale_drag(self, world_x: float, world_y: float):
        """Update scaling while the user drags a scale handle."""
        layer_id = self.selected_layer_id
        if layer_id is None or not self.scale_dragging:
            return
        cx, cy = self.scale_drag_center
        min_scale = 0.05
        max_scale = 5.0
        targets = self._current_drag_targets or [layer_id]
        initial_map = self.scale_drag_initials or {
            layer_id: self.layer_scale_offsets.get(layer_id, (1.0, 1.0))
        }

        def clamp(value: float) -> float:
            return min(max_scale, max(min_scale, value))

        if self.scale_drag_axis == 'uniform':
            current = max(math.hypot(world_x - cx, world_y - cy), 0.001)
            ratio = current / self.scale_drag_start if self.scale_drag_start else 1.0
            for target in targets:
                init_sx, init_sy = initial_map.get(target, (1.0, 1.0))
                new_sx = clamp(init_sx * ratio)
                new_sy = clamp(init_sy * ratio)
                self.layer_scale_offsets[target] = (new_sx, new_sy)
        elif self.scale_drag_axis == 'x':
            delta = max(abs(world_x - cx), 0.001)
            ratio = delta / self.scale_drag_start if self.scale_drag_start else 1.0
            for target in targets:
                init_sx, init_sy = initial_map.get(target, (1.0, 1.0))
                new_sx = clamp(init_sx * ratio)
                current_y = self.layer_scale_offsets.get(target, (init_sx, init_sy))[1]
                self.layer_scale_offsets[target] = (new_sx, current_y)
        elif self.scale_drag_axis == 'y':
            delta = max(abs(world_y - cy), 0.001)
            ratio = delta / self.scale_drag_start if self.scale_drag_start else 1.0
            for target in targets:
                init_sx, init_sy = initial_map.get(target, (1.0, 1.0))
                new_sy = clamp(init_sy * ratio)
                current_x = self.layer_scale_offsets.get(target, (init_sx, init_sy))[0]
                self.layer_scale_offsets[target] = (current_x, new_sy)

    def _update_rotation_drag(self, world_x: float, world_y: float):
        """Update rotation offset while dragging the gizmo."""
        if self.dragged_layer_id is None:
            return
        center = self.get_layer_center(self.dragged_layer_id)
        if not center:
            return
        cx, cy = center
        angle = math.degrees(math.atan2(world_y - cy, world_x - cx))
        delta = angle - self.rotation_drag_last_angle
        # Normalize delta to [-180, 180] to avoid jumps
        while delta > 180.0:
            delta -= 360.0
        while delta < -180.0:
            delta += 360.0
        self.rotation_drag_accum += delta * self.drag_rotation_multiplier
        self.rotation_drag_last_angle = angle
        targets = self._current_drag_targets or (
            [self.dragged_layer_id] if self.dragged_layer_id is not None else []
        )
        if not targets:
            return
        for layer_id in targets:
            initial = self.rotation_initial_values.get(
                layer_id, self.layer_rotations.get(layer_id, 0.0)
            )
            self.layer_rotations[layer_id] = initial + self.rotation_drag_accum
        self.update()

    def _get_layer_anchor_value(self, layer_id: int) -> Optional[Tuple[float, float]]:
        """Return the effective anchor value (override or original) for a layer."""
        if layer_id in self.layer_anchor_overrides:
            return self.layer_anchor_overrides[layer_id]
        layer = self.get_layer_by_id(layer_id)
        if not layer:
            return None
        return (layer.anchor_x, layer.anchor_y)

    def _set_layer_anchor_override(self, layer_id: int, anchor: Tuple[float, float]):
        """Set or clear the anchor override for a layer."""
        layer = self.get_layer_by_id(layer_id)
        if not layer:
            return
        if (
            abs(anchor[0] - layer.anchor_x) < 1e-4
            and abs(anchor[1] - layer.anchor_y) < 1e-4
        ):
            self.layer_anchor_overrides.pop(layer_id, None)
        else:
            self.layer_anchor_overrides[layer_id] = (anchor[0], anchor[1])

    def _capture_transform_state(self, layer_ids: List[int]) -> Dict:
        offsets = {}
        rotations = {}
        scales = {}
        anchors = {}
        for layer_id in layer_ids:
            offsets[layer_id] = tuple(self.layer_offsets.get(layer_id, (0.0, 0.0)))
            rotations[layer_id] = float(self.layer_rotations.get(layer_id, 0.0))
            scales[layer_id] = tuple(self.layer_scale_offsets.get(layer_id, (1.0, 1.0)))
            if layer_id in self.layer_anchor_overrides:
                anchors[layer_id] = tuple(self.layer_anchor_overrides[layer_id])
            else:
                anchors[layer_id] = None
        return {'offsets': offsets, 'rotations': rotations, 'scales': scales, 'anchors': anchors}

    def _begin_transform_action(self, layer_ids: List[int]):
        unique = sorted(set(layer_ids))
        if not unique:
            self._active_transform_ids = []
            self._active_transform_snapshot = None
            return
        self._active_transform_ids = unique
        self._active_transform_snapshot = self._capture_transform_state(unique)

    def _end_transform_action(self):
        if not self._active_transform_snapshot:
            self._active_transform_ids = []
            self._current_drag_targets = []
            return
        after_state = self._capture_transform_state(self._active_transform_ids)
        if after_state != self._active_transform_snapshot:
            action = {
                'layer_ids': tuple(self._active_transform_ids),
                'before': self._active_transform_snapshot,
                'after': after_state,
            }
            self.transform_action_committed.emit(action)
        self._active_transform_snapshot = None
        self._active_transform_ids = []
        self._current_drag_targets = []

    def apply_transform_snapshot(self, state: Dict):
        for layer_id, offset in state['offsets'].items():
            if abs(offset[0]) < 1e-6 and abs(offset[1]) < 1e-6:
                self.layer_offsets.pop(layer_id, None)
            else:
                self.layer_offsets[layer_id] = offset
        for layer_id, rotation in state['rotations'].items():
            if abs(rotation) < 1e-6:
                self.layer_rotations.pop(layer_id, None)
            else:
                self.layer_rotations[layer_id] = rotation
        for layer_id, scale in state.get('scales', {}).items():
            if abs(scale[0] - 1.0) < 1e-6 and abs(scale[1] - 1.0) < 1e-6:
                self.layer_scale_offsets.pop(layer_id, None)
            else:
                self.layer_scale_offsets[layer_id] = scale
        for layer_id, anchor in state.get('anchors', {}).items():
            if anchor is None:
                self.layer_anchor_overrides.pop(layer_id, None)
            else:
                self.layer_anchor_overrides[layer_id] = tuple(anchor)
        self.update()
    
    def _begin_anchor_drag(self, layer_id: int):
        """Start dragging an anchor handle."""
        if not self.player.animation:
            return
        state = self._last_layer_world_states.get(layer_id)
        if not state:
            return
        self.anchor_dragging = True
        self.anchor_drag_layer_id = layer_id
        self.anchor_drag_last_world = self._get_anchor_world_position(state, layer_id)
        self._begin_transform_action([layer_id])
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _update_anchor_drag(self, world_x: float, world_y: float):
        """Update anchor overrides while dragging."""
        if not self.anchor_dragging or self.anchor_drag_layer_id is None:
            return
        state = self._last_layer_world_states.get(self.anchor_drag_layer_id)
        if not state:
            return
        current_anchor = self._get_anchor_world_position(state, self.anchor_drag_layer_id)
        delta_world_x = world_x - current_anchor[0]
        delta_world_y = world_y - current_anchor[1]
        if abs(delta_world_x) < 1e-3 and abs(delta_world_y) < 1e-3:
            return
        m00 = state['m00']
        m01 = state['m01']
        m10 = state['m10']
        m11 = state['m11']
        det = m00 * m11 - m01 * m10
        if abs(det) < 1e-8:
            return
        inv00 = m11 / det
        inv01 = -m01 / det
        inv10 = -m10 / det
        inv11 = m00 / det
        delta_local_x = inv00 * delta_world_x + inv01 * delta_world_y
        delta_local_y = inv10 * delta_world_x + inv11 * delta_world_y
        scale_factor = max(self.renderer.base_world_scale * self.renderer.position_scale, 1e-6)
        delta_json_x = delta_local_x / scale_factor
        delta_json_y = delta_local_y / scale_factor
        precision = max(0.001, self.anchor_drag_precision)
        delta_json_x *= precision
        delta_json_y *= precision
        anchor_value = self._get_layer_anchor_value(self.anchor_drag_layer_id)
        if anchor_value is None:
            return
        new_anchor = (anchor_value[0] + delta_json_x, anchor_value[1] + delta_json_y)
        self._set_layer_anchor_override(self.anchor_drag_layer_id, new_anchor)
        self.anchor_drag_last_world = (world_x, world_y)
        self.update()

    def _end_anchor_drag(self):
        """Finish anchor dragging."""
        if not self.anchor_dragging:
            return
        self.anchor_dragging = False
        self.anchor_drag_layer_id = None
        self._end_transform_action()
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def _begin_parent_drag(self, layer_id: int, world_x: float, world_y: float):
        """Start dragging a parent handle."""
        self.parent_dragging = True
        self.parent_drag_layer_id = layer_id
        self.parent_drag_last_world = (world_x, world_y)
        self._begin_transform_action([layer_id])
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def _update_parent_drag(self, world_x: float, world_y: float):
        """Update parent offset while dragging."""
        if not self.parent_dragging or self.parent_drag_layer_id is None:
            return
        dx = (world_x - self.parent_drag_last_world[0]) * self.drag_translation_multiplier
        dy = (world_y - self.parent_drag_last_world[1]) * self.drag_translation_multiplier
        if abs(dx) < 1e-4 and abs(dy) < 1e-4:
            return
        layer_id = self.parent_drag_layer_id
        old_x, old_y = self.layer_offsets.get(layer_id, (0.0, 0.0))
        self.layer_offsets[layer_id] = (old_x + dx, old_y + dy)
        self.parent_drag_last_world = (world_x, world_y)
        self.update()

    def _end_parent_drag(self):
        """Finish parent dragging."""
        if not self.parent_dragging:
            return
        self.parent_dragging = False
        self.parent_drag_layer_id = None
        self._end_transform_action()
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def _get_drag_targets(self, base_layer_id: int) -> List[int]:
        if self.selection_group_lock and base_layer_id in self.selected_layer_ids:
            return list(self.selected_layer_ids)
        return [base_layer_id]
    
    def render_bone_overlay(self, anim_time: float):
        """
        Render bone/skeleton overlay showing layer hierarchy
        
        This draws:
        - Lines connecting parent layers to child layers (bones)
        - Circles at each layer's anchor point (joints)
        - Different colors for different hierarchy depths
        
        Args:
            anim_time: Current animation time
        """
        if not self.player.animation:
            return
        
        # Disable texturing for line/point drawing
        glDisable(GL_TEXTURE_2D)
        
        layer_world_states = self._build_layer_world_states(anim_time)
        layer_map = {layer.layer_id: layer for layer in self.player.animation.layers}
        
        # Colors for different hierarchy depths (rainbow-ish)
        depth_colors = [
            (1.0, 0.2, 0.2, 1.0),   # Red - root
            (1.0, 0.6, 0.2, 1.0),   # Orange
            (1.0, 1.0, 0.2, 1.0),   # Yellow
            (0.2, 1.0, 0.2, 1.0),   # Green
            (0.2, 1.0, 1.0, 1.0),   # Cyan
            (0.2, 0.2, 1.0, 1.0),   # Blue
            (1.0, 0.2, 1.0, 1.0),   # Magenta
            (1.0, 1.0, 1.0, 1.0),   # White
        ]
        
        # Calculate hierarchy depth for each layer
        depth_cache: Dict[int, int] = {}
        
        def get_depth(layer_id: int, visited: set = None) -> int:
            if layer_id in depth_cache:
                return depth_cache[layer_id]
            if visited is None:
                visited = set()
            if layer_id in visited:
                return 0  # Prevent infinite loops
            visited.add(layer_id)
            
            layer = layer_map.get(layer_id)
            if not layer or layer.parent_id < 0 or layer.parent_id not in layer_map:
                depth_cache[layer_id] = 0
                return 0
            depth = 1 + get_depth(layer.parent_id, visited)
            depth_cache[layer_id] = depth
            return depth
        
        # Get world position of a layer's anchor point
        def get_layer_world_pos(layer: LayerData) -> Tuple[float, float]:
            world_state = layer_world_states[layer.layer_id]
            return self._get_anchor_world_position(world_state, layer.layer_id)
        
        def draw_local_axes(layer: LayerData, base_pos: Tuple[float, float]):
            """Draw local X/Y axes for the layer to show orientation."""
            world_state = layer_world_states[layer.layer_id]
            axis_length = 25.0 / max(0.001, self.render_scale)
            m00 = world_state['m00']
            m01 = world_state['m01']
            m10 = world_state['m10']
            m11 = world_state['m11']
            
            # X axis (red)
            x_end = (
                base_pos[0] + m00 * axis_length,
                base_pos[1] + m10 * axis_length
            )
            glColor4f(1.0, 0.3, 0.3, 0.9)
            glBegin(GL_LINES)
            glVertex2f(base_pos[0], base_pos[1])
            glVertex2f(x_end[0], x_end[1])
            glEnd()
            
            # Y axis (green)
            y_end = (
                base_pos[0] + m01 * axis_length,
                base_pos[1] + m11 * axis_length
            )
            glColor4f(0.3, 1.0, 0.3, 0.9)
            glBegin(GL_LINES)
            glVertex2f(base_pos[0], base_pos[1])
            glVertex2f(y_end[0], y_end[1])
            glEnd()
        
        # Draw bones (lines from parent to child)
        glLineWidth(2.0)
        glBegin(GL_LINES)
        
        for layer in self.player.animation.layers:
            if layer.parent_id >= 0 and layer.parent_id in layer_map:
                parent_layer = layer_map[layer.parent_id]
                
                # Get positions
                child_pos = get_layer_world_pos(layer)
                parent_pos = get_layer_world_pos(parent_layer)
                
                # Get color based on child's depth
                depth = get_depth(layer.layer_id)
                color = depth_colors[depth % len(depth_colors)]
                
                # Draw line from parent to child
                glColor4f(*color)
                glVertex2f(parent_pos[0], parent_pos[1])
                glVertex2f(child_pos[0], child_pos[1])
        
        glEnd()
        
        # Draw joints (circles at anchor points)
        # We'll draw small squares since circles require more vertices
        joint_size = 6.0 / self.render_scale  # Size in world units
        
        for layer in self.player.animation.layers:
            pos = get_layer_world_pos(layer)
            depth = get_depth(layer.layer_id)
            color = depth_colors[depth % len(depth_colors)]
            
            # Draw filled square for joint
            glColor4f(*color)
            glBegin(GL_QUADS)
            glVertex2f(pos[0] - joint_size, pos[1] - joint_size)
            glVertex2f(pos[0] + joint_size, pos[1] - joint_size)
            glVertex2f(pos[0] + joint_size, pos[1] + joint_size)
            glVertex2f(pos[0] - joint_size, pos[1] + joint_size)
            glEnd()
            
            # Draw outline
            glColor4f(0.0, 0.0, 0.0, 1.0)
            glLineWidth(1.0)
            glBegin(GL_LINE_LOOP)
            glVertex2f(pos[0] - joint_size, pos[1] - joint_size)
            glVertex2f(pos[0] + joint_size, pos[1] - joint_size)
            glVertex2f(pos[0] + joint_size, pos[1] + joint_size)
            glVertex2f(pos[0] - joint_size, pos[1] + joint_size)
            glEnd()
            
            # Draw a smaller inner circle for root layers (no parent)
            if layer.parent_id < 0:
                inner_size = joint_size * 0.5
                glColor4f(1.0, 1.0, 1.0, 1.0)
                glBegin(GL_QUADS)
                glVertex2f(pos[0] - inner_size, pos[1] - inner_size)
                glVertex2f(pos[0] + inner_size, pos[1] - inner_size)
                glVertex2f(pos[0] + inner_size, pos[1] + inner_size)
                glVertex2f(pos[0] - inner_size, pos[1] + inner_size)
                glEnd()
            
            # Draw local axes to indicate orientation for this layer
            draw_local_axes(layer, pos)
        
        # Draw origin marker (crosshair at 0,0)
        origin_size = 20.0 / self.render_scale
        glColor4f(1.0, 1.0, 1.0, 0.5)
        glLineWidth(1.0)
        glBegin(GL_LINES)
        # Horizontal line
        glVertex2f(-origin_size, 0)
        glVertex2f(origin_size, 0)
        # Vertical line
        glVertex2f(0, -origin_size)
        glVertex2f(0, origin_size)
        glEnd()
        
        # Re-enable texturing
        glEnable(GL_TEXTURE_2D)
    
    def update_animation(self):
        """Update animation state with proper delta time"""
        current_time = time.time()

        if self.last_update_time is not None:
            delta_time = current_time - self.last_update_time
            delta_time = min(delta_time, 0.1)
        else:
            delta_time = 0.016

        self.last_update_time = current_time

        was_playing = self.player.playing
        previous_time = self.player.current_time

        if self.player.playing:
            self.player.update(delta_time)
            if self.player.animation:
                self.animation_time_changed.emit(self.player.current_time, self.player.duration)
                if self.player.loop and self.player.current_time + 1e-5 < previous_time:
                    self.animation_looped.emit()
            self.update()

        if was_playing != self.player.playing:
            self.playback_state_changed.emit(self.player.playing)
            if self.player.animation:
                self.animation_time_changed.emit(self.player.current_time, self.player.duration)
    
    def set_time(self, time: float):
        """
        Set current animation time
        
        Args:
            time: Time to set (in seconds)
        """
        self.player.current_time = time
        if self.player.animation:
            self.animation_time_changed.emit(self.player.current_time, self.player.duration)
        self.update()

    def set_antialiasing_enabled(self, enabled: bool):
        """Enable or disable OpenGL multisample anti-aliasing."""
        self.antialias_enabled = enabled
        self._apply_antialiasing_state()
        self.update()

    def set_scale_gizmo_enabled(self, enabled: bool):
        """Toggle the scale gizmo overlay."""
        self.scale_gizmo_enabled = enabled
        if not self.scale_gizmo_enabled:
            self.scale_dragging = False
        self.update()

    def set_anchor_overlay_enabled(self, enabled: bool):
        """Toggle anchor overlay visibility/editing."""
        self.anchor_overlay_enabled = enabled
        if not enabled and self.anchor_dragging:
            self._end_anchor_drag()
        if not enabled and self._anchor_hover_layer_id is not None:
            self._anchor_hover_layer_id = None
        self.update()

    def set_parent_overlay_enabled(self, enabled: bool):
        """Toggle parent overlay visibility/editing."""
        self.parent_overlay_enabled = enabled
        if not enabled and self.parent_dragging:
            self._end_parent_drag()
        self.update()

    def set_anchor_drag_precision(self, value: float):
        """Adjust how strongly mouse movement affects anchor edits."""
        clamped = max(0.001, min(5.0, float(value)))
        self.anchor_drag_precision = clamped

    def set_scale_gizmo_mode(self, mode: str):
        """Set how scaling is applied (uniform/per-axis)."""
        self.scale_mode = mode or "Uniform"
        self.update()

    def _apply_antialiasing_state(self):
        """Apply current antialiasing flag to the GL context."""
        try:
            if self.antialias_enabled:
                glEnable(GL_MULTISAMPLE)
            else:
                glDisable(GL_MULTISAMPLE)
        except Exception:
            # Some platforms may not expose GL_MULTISAMPLE; ignore failures
            pass

    def set_zoom_to_cursor(self, enabled: bool):
        """Enable or disable zooming towards the mouse cursor."""
        self.zoom_to_cursor = enabled

    def set_anchor_logging_enabled(self, enabled: bool):
        """Toggle renderer anchor logging for diagnostics."""
        self.renderer.enable_logging = enabled
        if not enabled:
            self.renderer.log_data.clear()
    
    # ========== Mouse Event Handlers ==========
    
    def mousePressEvent(self, event):
        """Handle mouse press for camera dragging or sprite dragging"""
        try:
            if event.button() == Qt.MouseButton.LeftButton:
                self._current_drag_targets = []
                # Left click - try to drag sprite
                mouse_x = event.position().x()
                mouse_y = event.position().y()
                
                # Convert mouse position to world space
                world_x, world_y = self.screen_to_world(mouse_x, mouse_y)

                if self.anchor_overlay_enabled:
                    anchor_hit = self._hit_anchor_handle(world_x, world_y)
                    if anchor_hit is not None:
                        self._begin_anchor_drag(anchor_hit)
                        event.accept()
                        return

                if self.parent_overlay_enabled:
                    parent_hit = self._hit_parent_handle(world_x, world_y)
                    if parent_hit is not None:
                        self._begin_parent_drag(parent_hit, world_x, world_y)
                        event.accept()
                        return

                # Check if rotation gizmo is active and clicked
                if self.rotation_gizmo_enabled and self.selected_layer_id is not None:
                    if self._is_point_on_rotation_handle(world_x, world_y):
                        center = self.get_layer_center(self.selected_layer_id)
                        if center:
                            cx, cy = center
                            self.rotation_dragging = True
                            self.dragging_sprite = False
                            self.dragged_layer_id = self.selected_layer_id
                            self.rotation_drag_last_angle = math.degrees(math.atan2(world_y - cy, world_x - cx))
                            self.rotation_drag_accum = 0.0
                            targets = self._get_drag_targets(self.dragged_layer_id)
                            self._current_drag_targets = targets
                            self.rotation_initial_values = {
                                layer_id: self.layer_rotations.get(layer_id, 0.0)
                                for layer_id in targets
                            }
                            self.setCursor(Qt.CursorShape.CrossCursor)
                            self._begin_transform_action(targets)
                            event.accept()
                            return

                if self.scale_gizmo_enabled and self.selected_layer_id is not None:
                    handle_hit = self._scale_handle_hit(world_x, world_y)
                    if handle_hit:
                        self._begin_scale_drag(handle_hit, world_x, world_y)
                        event.accept()
                        return

                # Determine which layer is under the cursor
                hit_layer = None
                allowed_ids = self.selected_layer_ids if self.selected_layer_ids else None
                
                if allowed_ids:
                    # If layers are selected, prioritize them for dragging
                    # First check if we hit any selected layer precisely
                    primary_layer = self.get_layer_by_id(self.selected_layer_id) if self.selected_layer_id else None
                    if primary_layer and primary_layer.layer_id in allowed_ids and self._check_layer_hit(world_x, world_y, primary_layer):
                        hit_layer = primary_layer
                    
                    if not hit_layer:
                        for layer_id in allowed_ids:
                            if self.selected_layer_id == layer_id:
                                continue
                            layer = self.get_layer_by_id(layer_id)
                            if layer and self._check_layer_hit(world_x, world_y, layer):
                                hit_layer = layer
                                break
                    
                    # If no precise hit but we have selected layers, use the primary selected layer
                    # This allows dragging selected layers from anywhere on screen
                    if not hit_layer and primary_layer and primary_layer.layer_id in allowed_ids:
                        hit_layer = primary_layer
                    elif not hit_layer and allowed_ids:
                        # Fall back to first selected layer if no primary
                        first_selected_id = next(iter(allowed_ids), None)
                        if first_selected_id is not None:
                            hit_layer = self.get_layer_by_id(first_selected_id)
                else:
                    hit_layer = self.find_layer_at_position(world_x, world_y)
                
                if hit_layer:
                    self.dragging_sprite = True
                    self.dragged_layer_id = hit_layer.layer_id
                    self.last_mouse_x = mouse_x
                    self.last_mouse_y = mouse_y
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                    self._current_drag_targets = self._get_drag_targets(self.dragged_layer_id)
                    self._begin_transform_action(self._current_drag_targets)
                    event.accept()
                else:
                    event.ignore()
                    
            elif event.button() == Qt.MouseButton.MiddleButton or event.button() == Qt.MouseButton.RightButton:
                # Right/middle click - camera drag
                self.dragging_camera = True
                self.last_mouse_x = event.position().x()
                self.last_mouse_y = event.position().y()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
            else:
                event.ignore()
        except Exception as e:
            print(f"Error in mousePressEvent: {e}")
            event.ignore()
    
    def mouseReleaseEvent(self, event):
        """Handle mouse release"""
        try:
            if event.button() == Qt.MouseButton.LeftButton:
                if self.anchor_dragging:
                    self._end_anchor_drag()
                    event.accept()
                    return
                if self.parent_dragging:
                    self._end_parent_drag()
                    event.accept()
                    return
                if self.scale_dragging:
                    self.scale_dragging = False
                    self._end_transform_action()
                    self.scale_drag_initials.clear()
                    event.accept()
                    return
                if self.rotation_dragging:
                    self.rotation_dragging = False
                    self._end_transform_action()
                    self.rotation_initial_values.clear()
                    self.dragged_layer_id = None
                    self.setCursor(Qt.CursorShape.ArrowCursor)
                    event.accept()
                elif self.dragging_sprite:
                    self.dragging_sprite = False
                    self._end_transform_action()
                    self.dragged_layer_id = None
                    self.setCursor(Qt.CursorShape.ArrowCursor)
                    event.accept()
                else:
                    event.ignore()
                    
            elif event.button() == Qt.MouseButton.MiddleButton or event.button() == Qt.MouseButton.RightButton:
                if self.dragging_camera:
                    self.dragging_camera = False
                    self.setCursor(Qt.CursorShape.ArrowCursor)
                    event.accept()
                else:
                    event.ignore()
            else:
                event.ignore()
        except Exception as e:
            print(f"Error in mouseReleaseEvent: {e}")
            event.ignore()
    
    def mouseMoveEvent(self, event):
        """Handle mouse move for camera panning or sprite dragging"""
        try:
            current_x = event.position().x()
            current_y = event.position().y()
            world_x, world_y = self.screen_to_world(current_x, current_y)

            hover_layer = self._hit_anchor_handle(world_x, world_y) if self.anchor_overlay_enabled else None
            if hover_layer != self._anchor_hover_layer_id:
                self._anchor_hover_layer_id = hover_layer
                self.update()

            if self.anchor_dragging and self.anchor_drag_layer_id is not None:
                self._update_anchor_drag(world_x, world_y)
                event.accept()
                return

            if self.parent_dragging and self.parent_drag_layer_id is not None:
                self._update_parent_drag(world_x, world_y)
                event.accept()
                return

            if self.scale_dragging and self.selected_layer_id is not None:
                self._update_scale_drag(world_x, world_y)
                self.update()
                event.accept()
                return
            
            if self.rotation_dragging and self.dragged_layer_id is not None:
                self._update_rotation_drag(world_x, world_y)
                event.accept()
            
            elif self.dragging_sprite and self.dragged_layer_id is not None:
                # Dragging a sprite
                dx = (current_x - self.last_mouse_x) / self.render_scale
                dy = (current_y - self.last_mouse_y) / self.render_scale
                dx *= self.drag_translation_multiplier
                dy *= self.drag_translation_multiplier
                
                move_targets = self._current_drag_targets or self._get_drag_targets(self.dragged_layer_id)

                for layer_id in move_targets:
                    old_x, old_y = self.layer_offsets.get(layer_id, (0.0, 0.0))
                    self.layer_offsets[layer_id] = (old_x + dx, old_y + dy)
                
                self.last_mouse_x = current_x
                self.last_mouse_y = current_y
                
                self.update()
                event.accept()
                
            elif self.dragging_camera:
                # Dragging camera
                dx = current_x - self.last_mouse_x
                dy = current_y - self.last_mouse_y
                
                self.camera_x += dx
                self.camera_y += dy
                
                self.last_mouse_x = current_x
                self.last_mouse_y = current_y
                
                self.update()
                event.accept()
            else:
                event.ignore()
        except Exception as e:
            print(f"Error in mouseMoveEvent: {e}")
            event.ignore()
    
    def wheelEvent(self, event):
        """Handle mouse wheel for zooming - keep animation centered."""
        center_world = None
        if self.player.animation:
            if getattr(self, 'zoom_to_cursor', False):
                cursor = event.position()
                center_world = self.screen_to_world(cursor.x(), cursor.y())
            else:
                center_world = self.get_animation_center()
        delta = event.angleDelta().y()
        if delta > 0:
            self.render_scale *= 1.1
        else:
            self.render_scale *= 0.9

        self.render_scale = max(0.001, self.render_scale)

        if center_world:
            center_screen = self.world_to_screen(*center_world)
            target_screen = (self.width() / 2, self.height() / 2)
            dx = target_screen[0] - center_screen[0]
            dy = target_screen[1] - center_screen[1]
            self.camera_x += dx
            self.camera_y += dy
        else:
            if delta > 0:
                self.camera_x *= 1.1
                self.camera_y *= 1.1
            else:
                self.camera_x *= 0.9
                self.camera_y *= 0.9

        self.update()
    
    def keyPressEvent(self, event):
        """Handle keyboard input"""
        try:
            if event.key() == Qt.Key.Key_L:
                # Enable logging for next frame
                print("=== L KEY PRESSED - LOGGING ENABLED ===")
                self.renderer.enable_logging = True
                self.renderer.log_data.clear()
                # Force immediate repaint (not just schedule it)
                self.repaint()
                # Now write the log
                print(f"Log data collected: {len(self.renderer.log_data)} entries")
                self.renderer.write_log_to_file("sprite_positions_NEW.txt")
                event.accept()
            else:
                event.ignore()
        except Exception as e:
            print(f"Error in keyPressEvent: {e}")
            import traceback
            traceback.print_exc()
            event.ignore()
    
    # ========== Helper Methods ==========
    
    def screen_to_world(self, screen_x: float, screen_y: float) -> Tuple[float, float]:
        """
        Convert screen coordinates to world coordinates
        
        The GL transform chain in paintGL is:
        1. Translate by (camera_x, camera_y)
        2. Scale by render_scale
        3. Translate by (w/2, h/2) if centered
        
        To invert, we reverse the order:
        1. Remove camera offset
        2. Divide by scale (to get into scaled space)
        3. Remove centering offset (which is in world space, applied after scale)
        
        Args:
            screen_x: X coordinate in screen space
            screen_y: Y coordinate in screen space
        
        Returns:
            Tuple of (world_x, world_y)
        """
        w = self.width()
        h = self.height()
        
        # Step 1: Remove camera offset (applied first in GL, so removed first here)
        sx = screen_x - self.camera_x
        sy = screen_y - self.camera_y
        
        # Step 2: Divide by scale to get into scaled space
        sx = sx / self.render_scale
        sy = sy / self.render_scale
        
        # Step 3: Remove centering (applied last in GL after scale, so it's in world units)
        # The centering translates by (w/2, h/2) in world space AFTER scaling
        # So we need to subtract (w/2, h/2) in world space
        if self.player.animation and self.player.animation.centered:
            sx -= w / 2
            sy -= h / 2
        
        return sx, sy

    def world_to_screen(self, world_x: float, world_y: float) -> Tuple[float, float]:
        """
        Convert world coordinates to screen coordinates.
        """
        w = self.width()
        h = self.height()

        sx = world_x
        sy = world_y
        if self.player.animation and self.player.animation.centered:
            sx += w / 2
            sy += h / 2

        sx *= self.render_scale
        sy *= self.render_scale

        sx += self.camera_x
        sy += self.camera_y
        return sx, sy

    def get_animation_center(self) -> Tuple[float, float]:
        """
        Estimate the center of the current animation by averaging layer positions.
        """
        if not self.player.animation:
            return (0.0, 0.0)

        layer_states = self._build_layer_world_states(self.player.current_time)
        if not layer_states:
            return (0.0, 0.0)

        xs = []
        ys = []
        for state in layer_states.values():
            xs.append(state.get('tx', 0.0))
            ys.append(state.get('ty', 0.0))

        min_x = min(xs)
        max_x = max(xs)
        min_y = min(ys)
        max_y = max(ys)

        return ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0)
    
    def find_layer_at_position(self, world_x: float, world_y: float) -> Optional[LayerData]:
        """
        Find which layer is at the given world position
        
        Args:
            world_x: X coordinate in world space
            world_y: Y coordinate in world space
        
        Returns:
            LayerData if found, None otherwise
        """
        if not self.player.animation:
            return None
        
        layer_world_states = self._build_layer_world_states()
        
        # Check layers in reverse order (front to back) to find topmost hit
        for layer in reversed(self.player.animation.layers):
            if layer.visible:
                world_state = layer_world_states[layer.layer_id]
                if self.renderer.is_point_in_layer(
                    world_x, world_y, layer, world_state,
                    self.texture_atlases, self.layer_offsets
                ):
                    return layer
        
        return None
    
    def _check_layer_hit(self, world_x: float, world_y: float, layer: LayerData, use_bounds_fallback: bool = True) -> bool:
        """
        Check if a point hits a specific layer
        
        Args:
            world_x: X coordinate in world space
            world_y: Y coordinate in world space
            layer: Layer to check
            use_bounds_fallback: If True, use bounding box check as fallback for selected layers
        
        Returns:
            True if point hits layer
        """
        # Use cached world states if available, otherwise build them
        if self._last_layer_world_states:
            world_state = self._last_layer_world_states.get(layer.layer_id)
        else:
            layer_world_states = self._build_layer_world_states()
            world_state = layer_world_states.get(layer.layer_id)
        
        if not world_state:
            return False
        
        # First try precise hit detection
        if self.renderer.is_point_in_layer(
            world_x, world_y, layer, world_state, 
            self.texture_atlases, self.layer_offsets
        ):
            return True
        
        # For selected layers, use bounding box as fallback with tolerance
        if use_bounds_fallback and layer.layer_id in self.selected_layer_ids:
            return self._check_layer_bounds_hit(world_x, world_y, layer, world_state)
        
        return False
    
    def _check_layer_bounds_hit(self, world_x: float, world_y: float, layer: LayerData, world_state: Dict) -> bool:
        """
        Check if a point is within the bounding box of a layer's sprite.
        This is more forgiving than pixel-perfect hit detection.
        
        Args:
            world_x: X coordinate in world space
            world_y: Y coordinate in world space
            layer: Layer to check
            world_state: Pre-calculated world state for the layer
        
        Returns:
            True if point is within layer bounds
        """
        sprite_name = world_state.get('sprite_name', '')
        if not sprite_name:
            return False
        
        # Find sprite in atlases
        sprite = None
        atlas = None
        override_atlases = self.layer_atlas_overrides.get(layer.layer_id)
        atlas_chain = (
            list(override_atlases) + self.texture_atlases
            if override_atlases
            else self.texture_atlases
        )
        for atl in atlas_chain:
            sprite = atl.get_sprite(sprite_name)
            if sprite:
                atlas = atl
                break
        
        if not sprite or not atlas:
            return False
        
        # Get local vertices
        corners_local = self.renderer.compute_local_vertices(sprite, atlas)
        if not corners_local or len(corners_local) < 4:
            return False
        
        # Transform corners to world space
        m00 = world_state['m00']
        m01 = world_state['m01']
        m10 = world_state['m10']
        m11 = world_state['m11']
        tx = world_state['tx']
        ty = world_state['ty']
        
        # Apply user offset
        user_offset_x, user_offset_y = self.layer_offsets.get(layer.layer_id, (0, 0))
        
        world_corners = []
        for lx, ly in corners_local:
            wx = m00 * lx + m01 * ly + tx + user_offset_x
            wy = m10 * lx + m11 * ly + ty + user_offset_y
            world_corners.append((wx, wy))
        
        # Calculate bounding box with tolerance
        min_x = min(c[0] for c in world_corners)
        max_x = max(c[0] for c in world_corners)
        min_y = min(c[1] for c in world_corners)
        max_y = max(c[1] for c in world_corners)
        
        # Add tolerance based on render scale (larger tolerance when zoomed out)
        tolerance = max(10.0 / max(self.render_scale, 0.1), 5.0)
        min_x -= tolerance
        max_x += tolerance
        min_y -= tolerance
        max_y += tolerance
        
        return min_x <= world_x <= max_x and min_y <= world_y <= max_y
    
    def get_layer_by_id(self, layer_id: int) -> Optional[LayerData]:
        """
        Get a layer by its ID
        
        Args:
            layer_id: ID of layer to find
        
        Returns:
            LayerData if found, None otherwise
        """
        if not self.player.animation:
            return None
        
        for layer in self.player.animation.layers:
            if layer.layer_id == layer_id:
                return layer
        return None
    
    # ========== Public Control Methods ==========
    
    def reset_camera(self):
        """Reset camera to default position"""
        self.camera_x = 0.0
        self.camera_y = 0.0
        self.render_scale = 1.0
        self.update()
    
    def fit_to_view(self, padding: float = 0.1) -> bool:
        """
        Center and scale the view to fit all visible sprites
        
        This calculates the bounding box of all visible sprites at the current
        animation time and adjusts the camera position and scale to fit them
        perfectly within the viewport.
        
        Args:
            padding: Extra padding as a fraction of the viewport (0.1 = 10% padding)
        
        Returns:
            True if successful, False if no animation loaded
        """
        if not self.player.animation:
            return False
        
        # Calculate bounding box of all visible sprites
        bounds = self.calculate_animation_bounds()
        
        if bounds is None:
            return False
        
        min_x, min_y, max_x, max_y = bounds
        
        # Calculate sprite dimensions
        sprite_width = max_x - min_x
        sprite_height = max_y - min_y
        
        if sprite_width <= 0 or sprite_height <= 0:
            return False
        
        # Get viewport dimensions
        viewport_width = self.width()
        viewport_height = self.height()
        
        # Calculate available space (with padding)
        available_width = viewport_width * (1 - 2 * padding)
        available_height = viewport_height * (1 - 2 * padding)
        
        # Calculate scale to fit
        scale_x = available_width / sprite_width
        scale_y = available_height / sprite_height
        
        # Use the smaller scale to ensure everything fits
        new_scale = min(scale_x, scale_y)
        
        # Calculate the center of the sprite bounds
        sprite_center_x = (min_x + max_x) / 2
        sprite_center_y = (min_y + max_y) / 2
        
        # Calculate camera position to center the sprite
        # The camera offset is applied before scaling in paintGL
        # We want the sprite center to appear at the viewport center
        
        # If animation is centered, the origin (0,0) is at viewport center
        # So we need to offset by the sprite center position
        if self.player.animation.centered:
            # Sprite center in world space needs to be at (0,0) in screen space
            # camera_x and camera_y are applied before scale
            self.camera_x = -sprite_center_x * new_scale + viewport_width / 2
            self.camera_y = -sprite_center_y * new_scale + viewport_height / 2
            # But wait, the centering translation is applied AFTER scale in paintGL
            # So we need to account for that
            self.camera_x = viewport_width / 2 - sprite_center_x * new_scale - (viewport_width / 2) * new_scale + (viewport_width / 2)
            self.camera_y = viewport_height / 2 - sprite_center_y * new_scale - (viewport_height / 2) * new_scale + (viewport_height / 2)
            # Simplify: we want sprite_center to appear at viewport center
            # After all transforms: screen_pos = (world_pos * scale + center_offset) + camera
            # We want: viewport_center = (sprite_center * scale + center_offset) + camera
            # So: camera = viewport_center - sprite_center * scale - center_offset
            # But center_offset = (w/2, h/2) is added in world space after scale
            # Actually let's recalculate properly:
            # In paintGL: translate(camera) -> scale -> translate(w/2, h/2)
            # So: screen = (world + w/2) * scale + camera... no wait
            # glTranslatef(camera_x, camera_y, 0) - this is in screen space
            # glScalef(scale) - scales everything
            # glTranslatef(w/2, h/2, 0) - this is in scaled world space
            # So: screen = camera + scale * (world + w/2, h/2)
            # We want: viewport_center = camera + scale * (sprite_center + w/2, h/2)
            # So: camera = viewport_center - scale * (sprite_center + w/2, h/2)
            # Hmm, but w/2 is viewport width, not world width...
            # Let me re-read paintGL...
            # The centering uses self.width()/2 which is viewport pixels
            # But it's applied after scale, so it's in world units that get scaled
            # Actually no - glTranslatef after glScalef means the translation is in the scaled coordinate system
            # So the w/2 translation is in world units, and then everything is scaled
            # 
            # Let me think differently:
            # Final screen position = camera + scale * (world_pos + center_offset)
            # where center_offset = (w/2, h/2) if centered
            # We want sprite_center to appear at viewport_center:
            # viewport_w/2 = camera_x + scale * (sprite_center_x + w/2)
            # camera_x = viewport_w/2 - scale * sprite_center_x - scale * w/2
            # But that doesn't seem right either because w/2 is in pixels...
            #
            # OK let me just do it empirically:
            # We want the sprite center to be at the screen center
            self.camera_x = (viewport_width / 2) - (sprite_center_x * new_scale) - (viewport_width / 2 * new_scale)
            self.camera_y = (viewport_height / 2) - (sprite_center_y * new_scale) - (viewport_height / 2 * new_scale)
        else:
            # No centering - origin is at top-left
            # screen = camera + scale * world
            # We want sprite_center at viewport_center:
            # viewport_w/2 = camera_x + scale * sprite_center_x
            self.camera_x = (viewport_width / 2) - (sprite_center_x * new_scale)
            self.camera_y = (viewport_height / 2) - (sprite_center_y * new_scale)
        
        self.render_scale = new_scale
        self.update()
        return True
    
    def calculate_animation_bounds(self) -> Optional[Tuple[float, float, float, float]]:
        """
        Calculate the bounding box of all visible sprites at current time
        
        Returns:
            Tuple of (min_x, min_y, max_x, max_y) in world coordinates,
            or None if no visible sprites
        """
        if not self.player.animation:
            return None
        
        layer_world_states = self._build_layer_world_states()
        
        min_x = float('inf')
        min_y = float('inf')
        max_x = float('-inf')
        max_y = float('-inf')
        
        found_any = False
        
        for layer in self.player.animation.layers:
            if not layer.visible:
                continue
            
            world_state = layer_world_states[layer.layer_id]
            sprite_name = world_state['sprite_name']
            
            if not sprite_name:
                continue
            
            # Find sprite in atlases
            sprite = None
            atlas = None
            for atl in self.texture_atlases:
                sprite = atl.get_sprite(sprite_name)
                if sprite:
                    atlas = atl
                    break
            
            if not sprite or not atlas:
                continue
            
            corners_local = self.renderer.compute_local_vertices(sprite, atlas)
            if not corners_local:
                continue
            
            # Transform corners to world space using the matrix
            m00 = world_state['m00']
            m01 = world_state['m01']
            m10 = world_state['m10']
            m11 = world_state['m11']
            tx = world_state['tx']
            ty = world_state['ty']
            
            # Apply user offset
            user_offset_x, user_offset_y = self.layer_offsets.get(layer.layer_id, (0, 0))
            
            for lx, ly in corners_local:
                # Transform to world space
                wx = m00 * lx + m01 * ly + tx + user_offset_x
                wy = m10 * lx + m11 * ly + ty + user_offset_y
                
                min_x = min(min_x, wx)
                min_y = min(min_y, wy)
                max_x = max(max_x, wx)
                max_y = max(max_y, wy)
                found_any = True
        
        if not found_any:
            return None
        
        return (min_x, min_y, max_x, max_y)
    
    def reset_layer_offsets(self):
        """Reset all layer offsets to default"""
        self.layer_offsets.clear()
        self.layer_rotations.clear()
        self.layer_scale_offsets.clear()
        self.layer_anchor_overrides.clear()
        self.rotation_initial_values.clear()
        self.scale_drag_initials.clear()
        self.rotation_dragging = False
        self.dragging_sprite = False
        self.scale_dragging = False
        self.anchor_dragging = False
        self.parent_dragging = False
        self.anchor_drag_layer_id = None
        self.parent_drag_layer_id = None
        self.update()

    def set_layer_atlas_overrides(self, overrides: Dict[int, List[TextureAtlas]]):
        """Assign per-layer atlas priority overrides."""
        if overrides:
            self.layer_atlas_overrides = {
                layer_id: list(atlases) for layer_id, atlases in overrides.items()
            }
        else:
            self.layer_atlas_overrides = {}
        self.update()

    def set_layer_pivot_context(self, context: Dict[int, bool]):
        """Track which layers have sheet+sprite remaps for pivot gating."""
        if context:
            self.layer_pivot_context = dict(context)
        else:
            self.layer_pivot_context = {}
        self.update()

    def set_shader_registry(self, registry: Optional[ShaderRegistry]):
        """Update shader registry on the renderer."""
        self.renderer.set_shader_registry(registry)

    def set_costume_pivot_adjustment_enabled(self, enabled: bool):
        """Toggle costume pivot adjustments in the renderer."""
        self.renderer.set_costume_pivot_adjustment_enabled(enabled)
        self.update()

    def set_costume_attachments(
        self,
        payloads: List[Dict[str, Any]],
        layers: List[LayerData]
    ):
        """Install attachment animations described by the costume parser."""
        self.attachment_instances.clear()
        if not payloads:
            self.update()
            return

        name_lookup = {layer.name.lower(): layer.layer_id for layer in layers}
        for payload in payloads:
            if not payload.get("target_layer"):
                continue
            if payload.get("target_layer_id") is None:
                target = name_lookup.get(payload["target_layer"].lower())
                if target is not None:
                    payload["target_layer_id"] = target

        # Ensure textures are uploaded before we store the instances.
        self.makeCurrent()
        try:
            for payload in payloads:
                for atlas in payload.get("atlases", []):
                    if isinstance(atlas, TextureAtlas) and atlas.texture_id is None:
                        atlas.load_texture()
        finally:
            self.doneCurrent()

        instances: List[AttachmentInstance] = []
        for payload in payloads:
            animation: Optional[AnimationData] = payload.get("animation")
            if not animation:
                continue
            player = AnimationPlayer()
            # Inherit widget-level tweening setting so attachments match main playback
            player.tweening_enabled = getattr(self, 'tweening_enabled', True)
            player.load_animation(animation)
            loop_flag = bool(payload.get("loop", True))
            player.loop = loop_flag
            raw_offset = payload.get("time_offset", payload.get("time_scale", 0.0))
            try:
                offset_value = float(raw_offset)
            except (TypeError, ValueError):
                offset_value = 0.0
            tempo_multiplier = payload.get("tempo_multiplier", 1.0)
            try:
                tempo_value = float(tempo_multiplier)
            except (TypeError, ValueError):
                tempo_value = 1.0
            tempo_value = max(0.1, tempo_value)
            instances.append(
                AttachmentInstance(
                    name=payload.get("name", "attachment"),
                    target_layer=payload.get("target_layer", ""),
                    target_layer_id=payload.get("target_layer_id"),
                    player=player,
                    atlases=list(payload.get("atlases", [])),
                    time_offset=offset_value,
                    tempo_multiplier=tempo_value,
                    loop=loop_flag,
                    root_layer_name=payload.get("root_layer"),
                    allow_base_fallback=bool(payload.get("allow_base_fallback")),
                )
            )
        self.attachment_instances = instances
        self.update()

    def set_tweening_enabled(self, enabled: bool):
        """Enable or disable linear interpolation (tweening) for all players."""
        self.tweening_enabled = bool(enabled)
        try:
            self.player.tweening_enabled = self.tweening_enabled
        except Exception:
            pass
        for inst in getattr(self, 'attachment_instances', []):
            try:
                inst.player.tweening_enabled = self.tweening_enabled
            except Exception:
                continue
        self.update()

    def set_glitch_jitter_enabled(self, enabled: bool):
        """Enable or disable per-frame jitter for players."""
        self.glitch_jitter_enabled = bool(enabled)
        try:
            self.player.glitch_jitter_enabled = self.glitch_jitter_enabled
            self.player.jitter_amplitude = float(getattr(self, 'jitter_amplitude', 1.0))
        except Exception:
            pass
        for inst in getattr(self, 'attachment_instances', []):
            try:
                inst.player.glitch_jitter_enabled = self.glitch_jitter_enabled
                inst.player.jitter_amplitude = float(getattr(self, 'jitter_amplitude', 1.0))
            except Exception:
                continue
        self.update()

    def set_glitch_jitter_amount(self, amount: float):
        try:
            self.jitter_amplitude = float(amount)
        except Exception:
            self.jitter_amplitude = 1.0
        try:
            self.player.jitter_amplitude = self.jitter_amplitude
        except Exception:
            pass
        for inst in getattr(self, 'attachment_instances', []):
            try:
                inst.player.jitter_amplitude = self.jitter_amplitude
            except Exception:
                continue
        self.update()

    def set_glitch_sprite_enabled(self, enabled: bool):
        self.glitch_sprite_enabled = bool(enabled)
        try:
            self.player.glitch_sprite_enabled = self.glitch_sprite_enabled
            self.player.glitch_sprite_chance = float(getattr(self, 'glitch_sprite_chance', 0.1))
        except Exception:
            pass
        for inst in getattr(self, 'attachment_instances', []):
            try:
                inst.player.glitch_sprite_enabled = self.glitch_sprite_enabled
                inst.player.glitch_sprite_chance = float(getattr(self, 'glitch_sprite_chance', 0.1))
            except Exception:
                continue
        self.update()

    def set_glitch_sprite_chance(self, chance: float):
        try:
            self.glitch_sprite_chance = float(chance)
        except Exception:
            self.glitch_sprite_chance = 0.1
        try:
            self.player.glitch_sprite_chance = self.glitch_sprite_chance
        except Exception:
            pass
        for inst in getattr(self, 'attachment_instances', []):
            try:
                inst.player.glitch_sprite_chance = self.glitch_sprite_chance
            except Exception:
                continue
        self.update()
    
    def set_selection_state(self, layer_ids: Set[int], primary_id: Optional[int], lock: bool):
        """Update which layers are selectable and whether they move together."""
        self.selected_layer_ids = set(layer_ids)
        if primary_id in self.selected_layer_ids:
            self.selected_layer_id = primary_id
        else:
            self.selected_layer_id = next(iter(self.selected_layer_ids), None)
        self.selection_group_lock = lock and bool(self.selected_layer_ids)
        if not self.selected_layer_id:
            self.scale_dragging = False
        self.update()

    def set_selected_layer(self, layer_id: Optional[int]):
        """Convenience helper for single-layer selection."""
        if layer_id is None:
            self.set_selection_state(set(), None, False)
        else:
            self.set_selection_state({layer_id}, layer_id, False)
    
    #
