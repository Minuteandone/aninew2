"""
Sprite Renderer
Pure rendering logic for drawing sprites with OpenGL
Separated from widget logic for clarity and testability
"""

import math
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple, Any
from OpenGL.GL import *
import numpy as np
from PIL import Image

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception as heif_exc:  # pragma: no cover - optional dependency
    print(f"Warning: Failed to enable HEIF/AVIF support in renderer: {heif_exc}")

from core.data_structures import LayerData, SpriteInfo
from core.texture_atlas import TextureAtlas
from core.animation_player import AnimationPlayer
from utils.shader_registry import ShaderPreset, ShaderRegistry, ShaderBehavior


# Blend mode constants matching MSM game engine
# From sys::gfx::OpenGLState::BlendMode::realSet in FunctionsAll.txt
class BlendMode:
    """
    Blend mode enum matching MSM game engine values.
    
    Mapping derived from sys::gfx::OpenGLState::BlendMode::realSet in FunctionsAll.txt.
    The engine supports a handful of blend ids that ultimately boil down to a few
    glBlendFunc combinations:
        - default/0    -> GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA
        - 1/3/4        -> GL_ONE, GL_ONE_MINUS_SRC_ALPHA (premult alpha)
        - 2            -> additive (GL_ONE, GL_ONE)
        - 5            -> inherit/leave current GL state
        - 6            -> multiply (GL_DST_COLOR, GL_ONE)
        - 7            -> screen-ish (GL_DST_COLOR, GL_ONE_MINUS_SRC_ALPHA)
    
    We keep textures in premultiplied form, so we treat the default mode the same
    as the premult paths inside the viewer to match MSM visuals.
    """
    STANDARD = 0
    PREMULT_ALPHA = 1
    ADDITIVE = 2
    PREMULT_ALPHA_ALT = 3
    PREMULT_ALPHA_ALT2 = 4
    INHERIT = 5
    MULTIPLY = 6
    SCREEN = 7


def set_blend_mode(blend_mode: int):
    """
    Set OpenGL blend function based on blend mode value.
    
    This matches the MSM game engine's blend mode handling.
    The game uses premultiplied alpha textures, so the blend functions
    are designed for that format.
    
    Args:
        blend_mode: Blend mode value from layer data
    """
    if blend_mode in (BlendMode.STANDARD,
                      BlendMode.PREMULT_ALPHA,
                      BlendMode.PREMULT_ALPHA_ALT,
                      BlendMode.PREMULT_ALPHA_ALT2,
                      BlendMode.INHERIT):
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
    elif blend_mode == BlendMode.ADDITIVE:
        glBlendFunc(GL_ONE, GL_ONE)
    elif blend_mode == BlendMode.MULTIPLY:
        glBlendFunc(GL_DST_COLOR, GL_ONE)
    elif blend_mode == BlendMode.SCREEN:
        glBlendFunc(GL_DST_COLOR, GL_ONE_MINUS_SRC_ALPHA)
    elif blend_mode == BlendMode.INHERIT:
        return
    else:
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)


def reset_blend_mode():
    """Reset to default blend mode (premultiplied alpha)"""
    glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)


@dataclass
class SpriteDrawInfo:
    """Capture sprite geometry and color data for shader overlays."""

    vertices: List[Tuple[float, float]]
    texcoords: List[Tuple[float, float]]
    triangles: List[int]
    color: Tuple[float, float, float, float]
    sprite: SpriteInfo
    atlas: TextureAtlas


@dataclass
class ShaderOverlayTexture:
    """Simple container for shader sequence textures."""

    path: str
    texture_id: Optional[int] = None
    width: int = 0
    height: int = 0

    def ensure_loaded(self) -> bool:
        if self.texture_id:
            return True
        try:
            image = Image.open(self.path)
            image = image.convert('RGBA')
            img_data = np.array(image, dtype=np.float32) / 255.0
            alpha = img_data[:, :, 3:4]
            img_data[:, :, 0:3] *= alpha
            img_data = (img_data * 255.0).astype(np.uint8)
            self.texture_id = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, self.texture_id)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, image.width, image.height, 0, GL_RGBA, GL_UNSIGNED_BYTE, img_data)
            self.width = image.width
            self.height = image.height
            return True
        except Exception as exc:
            print(f"Failed to load shader texture '{self.path}': {exc}")
            return False


class SpriteRenderer:
    """
    Handles sprite rendering logic
    Separated from OpenGL widget for better organization
    """
    
    def __init__(self):
        self.position_scale: float = 1.0
        self.base_world_scale: float = 1.0  # Set to 1.0 - JSON coordinates are already correct
        self.enable_logging: bool = False
        self.force_opaque: bool = False
        self.log_data: list = []
        self.anchor_bias_x: float = 0.0
        self.anchor_bias_y: float = 0.0
        self.local_position_multiplier: float = 1.0
        self.parent_mix: float = 1.0
        self.rotation_bias: float = 0.0
        self.scale_bias_x: float = 1.0
        self.scale_bias_y: float = 1.0
        self.world_offset_x: float = 0.0
        self.world_offset_y: float = 0.0
        self.trim_shift_multiplier: float = 1.0
        self.anchor_overrides: Dict[int, Tuple[float, float]] = {}
        self.shader_registry: Optional[ShaderRegistry] = None
        self._missing_shader_warnings: Set[str] = set()
        self._sprite_alias_cache: Dict[Tuple[str, str], str] = {}
        self._sprite_alias_warnings: Set[Tuple[str, str]] = set()
        self.shader_texture_cache: Dict[str, ShaderOverlayTexture] = {}
        self.current_time: float = 0.0
        self.animation_duration: float = 0.0
        self.costume_pivot_adjustment_enabled: bool = False
        self.pending_mask_key: Optional[str] = None
        self._mask_warning_messages: Set[str] = set()

    def reset_layer_masks(self) -> None:
        """Clear any pending layer mask state before a new frame renders."""
        self.pending_mask_key = None

    def _atlas_cache_key(self, atlas: Optional[TextureAtlas]) -> str:
        """Return a stable cache key for atlas-scoped lookups."""
        if not atlas:
            return "<none>"
        source = getattr(atlas, "source_name", None)
        if source:
            return str(source).strip().lower()
        image_path = getattr(atlas, "image_path", "") or ""
        if image_path:
            return os.path.basename(image_path).lower()
        return f"atlas_{id(atlas)}"

    def _sprite_alias_candidates(self, sprite_name: str) -> List[str]:
        """
        Generate fallback sprite names.
        Handles cases like JM_1st_hand_02.png -> JM_1st_hand.
        """
        base = (sprite_name or "").strip()
        if not base:
            return []
        seen: Set[str] = set()
        candidates: List[str] = []

        def _enqueue(value: str):
            value = value.strip()
            if not value or value in seen:
                return
            seen.add(value)
            candidates.append(value)

        _enqueue(base)
        if "." in base:
            _enqueue(base.rsplit(".", 1)[0])

        # Some atlas exports bake extensions into sprite names (e.g., JM_1st_hand.png).
        if "." not in base:
            for ext in (".png", ".avif", ".webp"):
                _enqueue(base + ext)

        numeric_working = base
        while True:
            reduced = re.sub(r"([ _-])\d+$", "", numeric_working)
            if reduced == numeric_working or not reduced:
                break
            _enqueue(reduced)
            numeric_working = reduced

        alpha_working = base
        while True:
            reduced = re.sub(r"([ _-])[A-Za-z]$", "", alpha_working)
            if reduced == alpha_working or not reduced:
                break
            _enqueue(reduced)
            alpha_working = reduced

        return [name for name in candidates if name != base]

    def _compute_sprite_alias(
        self,
        sprite_name: str,
        atlas: Optional[TextureAtlas]
    ) -> Tuple[Optional[str], Optional[SpriteInfo]]:
        """Return (alias_name, sprite) if a fallback sprite exists."""
        if not atlas:
            return None, None
        for candidate in self._sprite_alias_candidates(sprite_name):
            sprite = atlas.get_sprite(candidate)
            if sprite:
                return candidate, sprite
        return None, None

    def _get_sprite_from_atlas(
        self,
        sprite_name: str,
        atlas: Optional[TextureAtlas],
        allow_alias: bool = True
    ) -> Tuple[Optional[SpriteInfo], str]:
        """Lookup sprite in atlas, falling back to aliases that strip suffixes."""
        if not sprite_name or not atlas:
            return None, sprite_name

        atlas_key = self._atlas_cache_key(atlas)
        cache_key = (atlas_key, sprite_name)
        cached = self._sprite_alias_cache.get(cache_key)
        if cached is not None:
            if cached:
                sprite = atlas.get_sprite(cached)
                if sprite:
                    return sprite, cached
            else:
                return None, sprite_name

        sprite = atlas.get_sprite(sprite_name)
        if sprite:
            self._sprite_alias_cache[cache_key] = sprite_name
            return sprite, sprite_name

        if not allow_alias:
            self._sprite_alias_cache[cache_key] = ""
            return None, sprite_name

        alias_name, alias_sprite = self._compute_sprite_alias(sprite_name, atlas)
        if alias_name and alias_sprite:
            self._sprite_alias_cache[cache_key] = alias_name
            warn_key = (sprite_name.lower(), atlas_key)
            if warn_key not in self._sprite_alias_warnings:
                sheet_label = getattr(atlas, "source_name", None) or os.path.basename(
                    getattr(atlas, "image_path", "") or ""
                ) or "atlas"
                print(f"[SpriteAlias] '{sprite_name}' missing in {sheet_label}; using '{alias_name}'.")
                self._sprite_alias_warnings.add(warn_key)
            return alias_sprite, alias_name

        self._sprite_alias_cache[cache_key] = ""
        return None, sprite_name

    def _find_sprite_in_atlases(
        self,
        sprite_name: str,
        atlases: Optional[List[TextureAtlas]],
        allow_alias: bool = True
    ) -> Tuple[Optional[SpriteInfo], Optional[TextureAtlas], str]:
        """Search all atlases in order, returning the first match (with alias support)."""
        if not sprite_name or not atlases:
            return None, None, sprite_name
        for atlas in atlases:
            sprite, resolved_name = self._get_sprite_from_atlas(sprite_name, atlas, allow_alias)
            if sprite:
                return sprite, atlas, resolved_name
        return None, None, sprite_name

    def _get_hires_scale(self, atlas: TextureAtlas) -> float:
        """Return the scale applied to hi-res atlases."""
        return 0.5 if atlas.is_hires else 1.0

    def _build_polygon_local_vertices(
        self, sprite: SpriteInfo, atlas: TextureAtlas
    ) -> List[Tuple[float, float]]:
        """Return polygon vertices in local sprite space, scaled appropriately."""
        if not sprite.vertices:
            return []
        scale = self._get_hires_scale(atlas) * self.position_scale
        trim_shift = self.trim_shift_multiplier
        offset_dx = sprite.offset_x * trim_shift - sprite.offset_x
        offset_dy = sprite.offset_y * trim_shift - sprite.offset_y
        return [
            ((vx + offset_dx) * scale, (vy + offset_dy) * scale)
            for vx, vy in sprite.vertices
        ]

    def _build_quad_local_vertices(
        self, sprite: SpriteInfo, atlas: TextureAtlas
    ) -> List[Tuple[float, float]]:
        """Return quad vertices for sprites without polygon meshes."""
        scale = self._get_hires_scale(atlas) * self.position_scale
        trim_shift = self.trim_shift_multiplier
        base_x = sprite.offset_x * trim_shift
        base_y = sprite.offset_y * trim_shift

        if sprite.rotated:
            width = sprite.h
            height = sprite.w
        else:
            width = sprite.w
            height = sprite.h

        corners = [
            (base_x, base_y),
            (base_x + width, base_y),
            (base_x + width, base_y + height),
            (base_x, base_y + height),
        ]
        return [(x * scale, y * scale) for x, y in corners]

    def compute_local_vertices(
        self, sprite: SpriteInfo, atlas: TextureAtlas
    ) -> List[Tuple[float, float]]:
        """
        Return the sprite's local-space vertices scaled with hi-res and position factors.
        Polygon meshes return their authored vertices; quads return the rectangle corners.
        """
        if sprite.has_polygon_mesh:
            return self._build_polygon_local_vertices(sprite, atlas)
        return self._build_quad_local_vertices(sprite, atlas)

    def _build_polygon_geometry(
        self, sprite: SpriteInfo, atlas: TextureAtlas
    ) -> Optional[Tuple[List[Tuple[float, float]], List[Tuple[float, float]], List[int]]]:
        """Return (vertices, texcoords, triangles) for polygon sprites if available."""
        if not sprite.has_polygon_mesh:
            return None
        vertices = self._build_polygon_local_vertices(sprite, atlas)
        if len(vertices) != len(sprite.vertices_uv) or len(sprite.triangles) < 3:
            return None
        return vertices, sprite.vertices_uv, sprite.triangles

    @staticmethod
    def _point_in_triangle(px: float, py: float, v0: Tuple[float, float],
                           v1: Tuple[float, float], v2: Tuple[float, float]) -> bool:
        """Return True if point lies inside the triangle defined by v0/v1/v2."""
        x0, y0 = v0
        x1, y1 = v1
        x2, y2 = v2
        denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(denom) < 1e-8:
            return False
        a = ((y1 - y2) * (px - x2) + (x2 - x1) * (py - y2)) / denom
        b = ((y2 - y0) * (px - x2) + (x0 - x2) * (py - y2)) / denom
        c = 1.0 - a - b
        epsilon = -1e-5
        return (a >= epsilon) and (b >= epsilon) and (c >= epsilon)
    
    def calculate_world_state(
        self,
        layer: LayerData,
        time: float,
        player: AnimationPlayer,
        layer_map: Dict[int, LayerData],
        world_states: Dict[int, Dict],
        atlases: List[TextureAtlas] = None,
        layer_atlas_overrides: Dict[int, List[TextureAtlas]] = None,
        pivot_remap_context: Optional[Dict[int, bool]] = None
    ) -> Dict:
        """
        Calculate the world-space state for a layer using 2x3 affine matrices
        
        CRITICAL INSIGHT FROM MSM GAME ENGINE:
        The game uses a standard 2D affine transformation system where:
        1. Each layer has an anchor point (pivot) that defines the rotation/scale center
        2. The position in keyframes is the position of the layer's anchor point
        3. Negative scale values are kept IN the matrix - they naturally cause mirroring
        
        The transformation order is: Translate to position -> Rotate -> Scale
        The anchor point offsets the sprite so rotation happens around the anchor.
        
        Matrix format:
        | m00  m01  tx |
        | m10  m11  ty |
        
        Child transform = Parent_Matrix × Child_Local_Matrix
        
        Args:
            layer: Layer to calculate state for
            time: Current animation time
            player: Animation player for interpolation
            layer_map: Map of layer IDs to layers
            world_states: Cache of already-calculated world states
        
        Returns:
            Dictionary containing world-space transform matrix and other properties
        """
        # Get this layer's local state from keyframes
        local_state = player.get_layer_state(layer, time)

        tint_r = tint_g = tint_b = tint_a = 1.0
        resolved_color = self._resolve_layer_color(layer, time)
        if resolved_color:
            try:
                tint_r, tint_g, tint_b, tint_a = resolved_color
            except (TypeError, ValueError):
                tint_r = tint_g = tint_b = tint_a = 1.0
        if tint_r != 1.0 or tint_g != 1.0 or tint_b != 1.0:
            local_state['r'] = int(max(0, min(255, round(local_state['r'] * tint_r))))
            local_state['g'] = int(max(0, min(255, round(local_state['g'] * tint_g))))
            local_state['b'] = int(max(0, min(255, round(local_state['b'] * tint_b))))
        
        # Extract local transform values
        # Apply base_world_scale to convert JSON coordinates to screen space
        pos_multiplier = self.local_position_multiplier
        pos_x = local_state['pos_x'] * pos_multiplier * self.base_world_scale * self.position_scale
        pos_y = local_state['pos_y'] * pos_multiplier * self.base_world_scale * self.position_scale
        rotation = local_state['rotation'] + self.rotation_bias
        scale_x = (local_state['scale_x'] / 100.0) * self.scale_bias_x
        scale_y = (local_state['scale_y'] / 100.0) * self.scale_bias_y
        opacity = (local_state['opacity'] / 100.0) * tint_a
        opacity = max(0.0, min(1.0, opacity))
        if self.force_opaque:
            opacity = 1.0
        
        # CRITICAL FIX: Keep negative scale values in the matrix!
        # The game engine does NOT separate flipping from the matrix transformation.
        # Negative scale naturally causes mirroring through the matrix itself.
        # This ensures the anchor point offset is calculated correctly.
        # DO NOT use abs() on scale values!
        
        # Layer anchor values - allow per-layer overrides for interactive editing
        if self.anchor_overrides and layer.layer_id in self.anchor_overrides:
            anchor_local_x, anchor_local_y = self.anchor_overrides[layer.layer_id]
        else:
            anchor_local_x = layer.anchor_x
            anchor_local_y = layer.anchor_y
        original_anchor_x = anchor_local_x
        original_anchor_y = anchor_local_y
        pivot_log: Optional[Dict[str, Any]] = None
        sprite_anchor_override: Optional[Tuple[float, float]] = None
        has_costume_pivot_context = bool(
            pivot_remap_context and pivot_remap_context.get(layer.layer_id)
        )

        # COSTUME SPRITE PIVOT ADJUSTMENT:
        # From FunctionsAll.txt RemapLayer function, when a costume sprite has a non-default
        # pivot (not 0.5, 0.5), the game adjusts the anchor:
        #   if ((pivot_x != 0.5) || (pivot_y != 0.5)) {
        #       setHAnchor(sprite_width * pivot_x - layer_anchor_x);
        #       setVAnchor(sprite_height * pivot_y - layer_anchor_y);
        #   }
        #
        # This means the NEW anchor = sprite_size * pivot - original_anchor
        # We need to apply this adjustment when rendering costume sprites with custom pivots.
        sprite_name = local_state['sprite_name']
        if sprite_name:
            # First check costume atlas overrides, then fall back to all atlases
            search_atlases: List[TextureAtlas] = []
            if layer_atlas_overrides and layer.layer_id in layer_atlas_overrides:
                search_atlases = layer_atlas_overrides[layer.layer_id]
            elif atlases:
                search_atlases = atlases
            found_sprite, search_atlas, resolved_sprite = self._find_sprite_in_atlases(
                sprite_name,
                search_atlases
            )
            if found_sprite and search_atlas:
                sprite = found_sprite
                # Check if pivot is non-default (not 0.5, 0.5)
                if abs(sprite.pivot_x - 0.5) > 0.001 or abs(sprite.pivot_y - 0.5) > 0.001:
                    if self.costume_pivot_adjustment_enabled and not has_costume_pivot_context:
                        # Costume logic only runs when both sheet + sprite remaps were supplied.
                        pass
                    else:
                        # Get sprite dimensions (use original dimensions, scaled for hires)
                        sprite_w = sprite.original_w if sprite.original_w > 0 else sprite.w
                        sprite_h = sprite.original_h if sprite.original_h > 0 else sprite.h
                        if sprite_w <= 0 and sprite.w > 0:
                            sprite_w = sprite.w
                        if sprite_h <= 0 and sprite.h > 0:
                            sprite_h = sprite.h
                        hires_scale = self._get_hires_scale(search_atlas)
                        sprite_w *= hires_scale
                        sprite_h *= hires_scale

                        # COSTUME SPRITE PIVOT FIX:
                        # Formula: new_anchor = sprite_size * pivot - old_anchor
                        if self.costume_pivot_adjustment_enabled:
                            adjusted_anchor_x = sprite_w * sprite.pivot_x - original_anchor_x
                            adjusted_anchor_y = sprite_h * sprite.pivot_y - original_anchor_y
                        else:
                            adjusted_anchor_x = sprite_w * sprite.pivot_x
                            adjusted_anchor_y = sprite_h * sprite.pivot_y
                        sprite_anchor_override = (
                            original_anchor_x - adjusted_anchor_x,
                            original_anchor_y - adjusted_anchor_y,
                        )
                        pivot_log = {
                            "sprite": sprite_name,
                            "resolved_sprite": resolved_sprite,
                            "pivot": (sprite.pivot_x, sprite.pivot_y),
                            "sprite_size": (sprite_w, sprite_h),
                            "original_anchor": (original_anchor_x, original_anchor_y),
                            "adjusted_anchor": (adjusted_anchor_x, adjusted_anchor_y),
                            "anchor_offset": sprite_anchor_override,
                        }

        anchor_x = (anchor_local_x + self.anchor_bias_x) * self.base_world_scale * self.position_scale
        anchor_y = (anchor_local_y + self.anchor_bias_y) * self.base_world_scale * self.position_scale
        
        # Log if enabled
        if self.enable_logging:
            log_entry: Dict[str, Any] = {
                'layer': layer.name,
                'layer_id': layer.layer_id,
                'original_anchor': (original_anchor_x, original_anchor_y),
                'layer_anchor': (anchor_local_x, anchor_local_y),
                'position': (pos_x, pos_y),
                'rotation': rotation,
                'scale': (scale_x, scale_y)
            }
            if pivot_log:
                log_entry['pivot_adjustment'] = pivot_log
                log_entry['sprite_anchor_offset'] = sprite_anchor_override
            self.log_data.append(log_entry)
        
        rot_rad = math.radians(rotation)
        cos_r = math.cos(rot_rad)
        sin_r = math.sin(rot_rad)
        
        # Build the transformation matrix
        # The standard 2D affine transform for rotation around a pivot point is:
        # T(pos) * R(angle) * S(scale) * T(-anchor)
        #
        # This means:
        # 1. First translate by -anchor (move anchor to origin)
        # 2. Then scale (including negative scale for mirroring!)
        # 3. Then rotate
        # 4. Finally translate to position
        #
        # The combined matrix is computed by multiplying these together
        
        # Build the 2x3 matrix components (rotation * scale)
        # Standard 2D rotation-scale matrix:
        # | cos*sx  -sin*sy |
        # | sin*sx   cos*sy |
        m00 = scale_x * cos_r
        m01 = -scale_y * sin_r
        m10 = scale_x * sin_r
        m11 = scale_y * cos_r
        
        # The translation combines position and the transformed anchor offset
        # T(pos) * R * S * T(-anchor) gives us:
        # tx = pos_x + (R*S * (-anchor)).x = pos_x - (m00*anchor_x + m01*anchor_y)
        # ty = pos_y + (R*S * (-anchor)).y = pos_y - (m10*anchor_x + m11*anchor_y)
        tx = pos_x - (m00 * anchor_x + m01 * anchor_y)
        ty = pos_y - (m10 * anchor_x + m11 * anchor_y)
        
        # Track anchor position in world space (before parent)
        child_anchor_world_x = pos_x
        child_anchor_world_y = pos_y

        # If this layer has a parent, multiply by parent's matrix
        child_m00 = m00
        child_m01 = m01
        child_m10 = m10
        child_m11 = m11
        child_tx = tx
        child_ty = ty

        parent_mix = max(0.0, min(1.0, self.parent_mix))

        world_anchor_x = child_anchor_world_x
        world_anchor_y = child_anchor_world_y

        if layer.parent_id >= 0 and layer.parent_id in layer_map:
            parent_layer = layer_map[layer.parent_id]
            
            # Get or calculate parent's world state
            if layer.parent_id in world_states:
                parent_state = world_states[layer.parent_id]
            else:
                parent_state = self.calculate_world_state(
                    parent_layer,
                    time,
                    player,
                    layer_map,
                    world_states,
                    atlases,
                    layer_atlas_overrides,
                    pivot_remap_context
                )
                world_states[layer.parent_id] = parent_state
            
            # Get parent's matrix
            p_m00 = parent_state['m00']
            p_m01 = parent_state['m01']
            p_m10 = parent_state['m10']
            p_m11 = parent_state['m11']
            p_tx = parent_state['tx']
            p_ty = parent_state['ty']
            
            # Matrix multiplication: Result = Parent × Child
            # | p_m00  p_m01  p_tx |   | m00  m01  tx |
            # | p_m10  p_m11  p_ty | × | m10  m11  ty |
            
            world_m00 = p_m00 * m00 + p_m01 * m10
            world_m01 = p_m00 * m01 + p_m01 * m11
            world_m10 = p_m10 * m00 + p_m11 * m10
            world_m11 = p_m10 * m01 + p_m11 * m11
            world_tx = p_m00 * tx + p_m01 * ty + p_tx
            world_ty = p_m10 * tx + p_m11 * ty + p_ty
            world_anchor_x = p_m00 * child_anchor_world_x + p_m01 * child_anchor_world_y + p_tx
            world_anchor_y = p_m10 * child_anchor_world_x + p_m11 * child_anchor_world_y + p_ty
            
            # NOTE: We do NOT multiply opacity from parent layers!
            # In MSM's animation system, each layer has its own independent opacity.
            # This allows child layers to be visible even when parent layers have opacity 0.
            # The parent layer with opacity 0 acts as a "null" or "control" layer that
            # only provides transform information without affecting child visibility.
            # This is how RARE/EPIC variants work - they hide the normal sprite layer
            # (opacity 0) but show the variant sprite layer (opacity 100) as a child.
        else:
            # No parent, local matrix IS world matrix
            world_m00 = m00
            world_m01 = m01
            world_m10 = m10
            world_m11 = m11
            world_tx = tx
            world_ty = ty

        if parent_mix < 1.0:
            mix_child = 1.0 - parent_mix
            world_m00 = world_m00 * parent_mix + child_m00 * mix_child
            world_m01 = world_m01 * parent_mix + child_m01 * mix_child
            world_m10 = world_m10 * parent_mix + child_m10 * mix_child
            world_m11 = world_m11 * parent_mix + child_m11 * mix_child
            world_tx = world_tx * parent_mix + child_tx * mix_child
            world_ty = world_ty * parent_mix + child_ty * mix_child
            world_anchor_x = world_anchor_x * parent_mix + child_anchor_world_x * mix_child
            world_anchor_y = world_anchor_y * parent_mix + child_anchor_world_y * mix_child

        world_tx += self.world_offset_x
        world_ty += self.world_offset_y
        world_anchor_x += self.world_offset_x
        world_anchor_y += self.world_offset_y
        
        return {
            # 2x3 transformation matrix
            'm00': world_m00,
            'm01': world_m01,
            'm10': world_m10,
            'm11': world_m11,
            'tx': world_tx,
            'ty': world_ty,
            'anchor_world_x': world_anchor_x,
            'anchor_world_y': world_anchor_y,
            # Other properties
            'world_opacity': opacity,
            'sprite_name': local_state['sprite_name'],
            'r': local_state['r'],
            'g': local_state['g'],
            'b': local_state['b'],
            'shader': getattr(layer, 'shader_name', None),
            'sprite_anchor_offset': sprite_anchor_override,
            # Debug info
            'rotation': rotation,
            'scale_x': scale_x,
            'scale_y': scale_y
        }

    def _resolve_layer_color(
        self,
        layer: LayerData,
        time: float
    ) -> Optional[Tuple[float, float, float, float]]:
        """Return the active tint, honoring gradients and animated overrides."""
        try:
            animator = getattr(layer, "color_animator", None)
            gradient = getattr(layer, "color_gradient", None)
            base_color = getattr(layer, "color_tint", None)

            if animator:
                animated = self._evaluate_color_animation(animator, time)
                if animated is not None:
                    return animated
            if gradient:
                ramp = self._evaluate_color_gradient(gradient, time)
                if ramp is not None:
                    return ramp
            return base_color
        except Exception as exc:  # pragma: no cover - diagnostics
            if self.enable_logging:
                self.log_data.append(f"Color resolve failed for {layer.name}: {exc}")
            return getattr(layer, "color_tint", None)

    def _evaluate_color_gradient(
        self,
        gradient: Dict[str, Any],
        time: float
    ) -> Optional[Tuple[float, float, float, float]]:
        stops = (gradient or {}).get("stops") or []
        if not stops:
            return None
        if len(stops) == 1:
            return tuple(stops[0].get("color") or (1.0, 1.0, 1.0, 1.0))  # type: ignore[return-value]

        period = gradient.get("period")
        if not period or period <= 0:
            period = gradient.get("duration") or self.animation_duration
        if not period or period <= 0:
            period = 1.0

        offset = gradient.get("offset", 0.0)
        speed = gradient.get("speed", 1.0)
        mode = gradient.get("mode") or ("pingpong" if gradient.get("ping_pong") else "loop")
        local_time = (time - offset) * speed

        if mode == "once" or gradient.get("loop") is False:
            normalized = max(0.0, min(1.0, local_time / period))
        elif mode == "pingpong":
            phase = (local_time / period) % 2.0
            normalized = 1.0 - abs(phase - 1.0)
        else:
            normalized = (local_time / period) % 1.0

        prev_stop = stops[0]
        next_stop = None
        for stop in stops:
            position = stop.get("position", 0.0)
            if position <= normalized:
                prev_stop = stop
            elif position > normalized:
                next_stop = stop
                break

        if next_stop is None:
            return tuple(prev_stop.get("color") or (1.0, 1.0, 1.0, 1.0))  # type: ignore[return-value]

        span = next_stop.get("position", 0.0) - prev_stop.get("position", 0.0)
        if span <= 0:
            return tuple(next_stop.get("color") or (1.0, 1.0, 1.0, 1.0))  # type: ignore[return-value]

        ratio = (normalized - prev_stop.get("position", 0.0)) / span
        interpolation = (prev_stop.get("interpolation") or next_stop.get("interpolation") or "").lower()
        if interpolation == "hold":
            return tuple(prev_stop.get("color") or (1.0, 1.0, 1.0, 1.0))  # type: ignore[return-value]
        return self._lerp_color(
            tuple(prev_stop.get("color") or (1.0, 1.0, 1.0, 1.0)),
            tuple(next_stop.get("color") or (1.0, 1.0, 1.0, 1.0)),
            max(0.0, min(1.0, ratio))
        )

    def _evaluate_color_animation(
        self,
        animator: Dict[str, Any],
        time: float
    ) -> Optional[Tuple[float, float, float, float]]:
        keyframes = (animator or {}).get("keyframes") or []
        if not keyframes:
            return None
        if len(keyframes) == 1:
            return tuple(keyframes[0].get("color") or (1.0, 1.0, 1.0, 1.0))  # type: ignore[return-value]

        duration = animator.get("duration")
        if not duration or duration <= 0:
            duration = keyframes[-1].get("time") or self.animation_duration
        if not duration or duration <= 0:
            duration = 1.0

        offset = animator.get("offset", 0.0)
        speed = animator.get("speed", 1.0)
        local_time = (time - offset) * speed

        if animator.get("loop", True):
            local_time = local_time % duration
        else:
            local_time = max(0.0, min(duration, local_time))

        prev_kf = keyframes[0]
        next_kf = None
        for frame in keyframes:
            key_time = frame.get("time", 0.0)
            if key_time <= local_time:
                prev_kf = frame
            elif key_time > local_time:
                next_kf = frame
                break

        if next_kf is None:
            return tuple(prev_kf.get("color") or (1.0, 1.0, 1.0, 1.0))  # type: ignore[return-value]

        span = next_kf.get("time", 0.0) - prev_kf.get("time", 0.0)
        if span <= 0:
            return tuple(next_kf.get("color") or (1.0, 1.0, 1.0, 1.0))  # type: ignore[return-value]

        ratio = (local_time - prev_kf.get("time", 0.0)) / span
        interpolation = (prev_kf.get("interpolation") or next_kf.get("interpolation") or "").lower()
        if interpolation == "hold":
            return tuple(prev_kf.get("color") or (1.0, 1.0, 1.0, 1.0))  # type: ignore[return-value]
        return self._lerp_color(
            tuple(prev_kf.get("color") or (1.0, 1.0, 1.0, 1.0)),
            tuple(next_kf.get("color") or (1.0, 1.0, 1.0, 1.0)),
            max(0.0, min(1.0, ratio))
        )

    @staticmethod
    def _lerp_color(
        a: Tuple[float, float, float, float],
        b: Tuple[float, float, float, float],
        t: float
    ) -> Tuple[float, float, float, float]:
        """Linear interpolation between two RGBA tuples."""
        return tuple(ai + (bi - ai) * t for ai, bi in zip(a, b))
    
    def render_layer(
        self,
        layer: LayerData,
        world_state: Dict,
        atlases: List[TextureAtlas],
        layer_offsets: Dict[int, Tuple[float, float]]
    ):
        """
        Render a single layer using its 2x3 transformation matrix
        
        Args:
            layer: Layer to render
            world_state: Pre-calculated world state with 2x3 matrix
            atlases: List of texture atlases to search for sprites
            layer_offsets: User-applied offsets for interactive dragging
        """
        mask_role = getattr(layer, "mask_role", None)
        mask_key = getattr(layer, "mask_key", None)
        if mask_role == "mask_source" and mask_key:
            self._render_mask_source_layer(layer, world_state, atlases, layer_offsets, mask_key)
            return

        shader_name = world_state.get('shader')
        shader_preset = self._get_shader_preset(shader_name)
        shader_behavior = self._get_shader_behavior(shader_name)
        blend_override = self._blend_value_from_preset(shader_preset)
        stencil_mask_active = False
        if mask_role == "mask_consumer" and mask_key:
            stencil_mask_active = self._activate_mask_consumer(mask_key)

        # Apply blend mode for this layer
        # The blend mode determines how this layer's pixels combine with the background
        # Common modes:
        # - 0: Normal (premultiplied alpha)
        # - 1: Additive (for glows - adds light without darkening)
        if blend_override is not None:
            set_blend_mode(blend_override)
        else:
            set_blend_mode(layer.blend_mode)
        
        glPushMatrix()
        
        # Apply user offset first (in world space)
        offset_x, offset_y = layer_offsets.get(layer.layer_id, (0, 0))
        if offset_x != 0 or offset_y != 0:
            glTranslatef(offset_x, offset_y, 0)
        
        # Apply the 2x3 transformation matrix using OpenGL
        # Convert 2x3 to 4x4 for OpenGL (add identity Z and W)
        # OpenGL uses column-major order, so we need to transpose
        # | m00  m01   0   tx |
        # | m10  m11   0   ty |
        # |  0    0    1    0 |
        # |  0    0    0    1 |
        matrix = [
            world_state['m00'], world_state['m10'], 0.0, 0.0,  # Column 1
            world_state['m01'], world_state['m11'], 0.0, 0.0,  # Column 2
            0.0, 0.0, 1.0, 0.0,                                 # Column 3
            world_state['tx'], world_state['ty'], 0.0, 1.0     # Column 4
        ]
        glMultMatrixf(matrix)
        
        # Render sprite - NO texture flipping needed, matrix handles mirroring!
        draw_info = self.render_sprite(
            world_state,
            world_state['world_opacity'],
            atlases,
            layer,
            render=not (shader_behavior and shader_behavior.replace_base_sprite),
            shader_behavior=shader_behavior,
        )
        if shader_behavior and draw_info:
            self._render_shader_overlay(draw_info, shader_behavior, shader_preset)
        
        glPopMatrix()
        self._deactivate_mask_consumer(stencil_mask_active)
        
        # Reset blend mode to default after rendering this layer
        # This ensures subsequent layers start with the correct blend state
        reset_blend_mode()
    
    def render_sprite(
        self,
        state: Dict,
        opacity: float,
        atlases: List[TextureAtlas],
        layer: LayerData = None,
        render: bool = True,
        shader_behavior: Optional[ShaderBehavior] = None
    ) -> Optional[SpriteDrawInfo]:
        """
        Render just the sprite at origin (transforms already applied)
        
        The transformation matrix already handles all positioning, rotation, and scaling
        including mirroring from negative scale values. We just draw the sprite at origin.
        
        Args:
            state: Layer state containing sprite info and colors
            opacity: Opacity value (0-1)
            atlases: List of texture atlases to search
            layer: Layer data containing anchor information
        """
        # Get sprite
        sprite_name = state['sprite_name']
        if not sprite_name:
            return
        
        # Find sprite in atlases
        sprite, atlas, _ = self._find_sprite_in_atlases(sprite_name, atlases)
        if not sprite or not atlas or not atlas.texture_id:
            return None
        
        # Apply opacity and color with premultiplied alpha
        # Since we're using glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA),
        # the color values need to be premultiplied by alpha
        r = state['r'] / 255.0 * opacity
        g = state['g'] / 255.0 * opacity
        b = state['b'] / 255.0 * opacity
        preset = self._get_shader_preset(state.get('shader'))
        if preset:
            r = min(1.0, r * preset.color_scale[0])
            g = min(1.0, g * preset.color_scale[1])
            b = min(1.0, b * preset.color_scale[2])
            opacity = max(0.0, min(1.0, opacity * preset.alpha_scale))
        if shader_behavior:
            wave = shader_behavior.color_wave_multiplier(self.current_time)
            if wave:
                r = max(0.0, min(1.0, r * wave[0]))
                g = max(0.0, min(1.0, g * wave[1]))
                b = max(0.0, min(1.0, b * wave[2]))
                if wave[3] != 1.0:
                    opacity = max(0.0, min(1.0, opacity * wave[3]))
        glColor4f(r, g, b, opacity)
        
        # Bind texture
        glBindTexture(GL_TEXTURE_2D, atlas.texture_id)
        anchor_dx = anchor_dy = 0.0
        anchor_offset = state.get('sprite_anchor_offset')
        if anchor_offset:
            anchor_dx = anchor_offset[0] * self.base_world_scale * self.position_scale
            anchor_dy = anchor_offset[1] * self.base_world_scale * self.position_scale

        geometry = self._build_polygon_geometry(sprite, atlas)
        if geometry:
            vertices, texcoords, triangles = geometry
            if anchor_dx or anchor_dy:
                vertices = [(x + anchor_dx, y + anchor_dy) for x, y in vertices]
            vert_count = len(vertices)
            if render:
                glBegin(GL_TRIANGLES)
                for i in range(0, len(triangles), 3):
                    idx0 = triangles[i]
                    idx1 = triangles[i + 1] if i + 1 < len(triangles) else None
                    idx2 = triangles[i + 2] if i + 2 < len(triangles) else None
                    if idx1 is None or idx2 is None:
                        break
                    if idx0 >= vert_count or idx1 >= vert_count or idx2 >= vert_count:
                        continue
                    u0, v0 = texcoords[idx0]
                    u1, v1 = texcoords[idx1]
                    u2, v2 = texcoords[idx2]
                    x0, y0 = vertices[idx0]
                    x1, y1 = vertices[idx1]
                    x2, y2 = vertices[idx2]
                    glTexCoord2f(u0, v0); glVertex2f(x0, y0)
                    glTexCoord2f(u1, v1); glVertex2f(x1, y1)
                    glTexCoord2f(u2, v2); glVertex2f(x2, y2)
                glEnd()
            return SpriteDrawInfo(vertices, texcoords, list(triangles), (r, g, b, opacity), sprite, atlas)
        
        # Calculate texture coordinates
        tx1 = sprite.x / atlas.image_width
        ty1 = sprite.y / atlas.image_height
        tx2 = (sprite.x + sprite.w) / atlas.image_width
        ty2 = (sprite.y + sprite.h) / atlas.image_height
        
        # Get sprite dimensions from atlas
        atlas_w = sprite.w
        atlas_h = sprite.h
        
        # When rotated in atlas, the sprite is stored rotated 90° clockwise
        if sprite.rotated:
            sprite_w = atlas_h  # After un-rotating, width comes from atlas height
            sprite_h = atlas_w  # After un-rotating, height comes from atlas width
        else:
            sprite_w = atlas_w
            sprite_h = atlas_h
        
        # Apply hi-res scaling: hi-res sprites are 2x size, need to be scaled down by 0.5
        hires_scale = 0.5 if atlas.is_hires else 1.0
        sprite_w *= hires_scale
        sprite_h *= hires_scale
        
        # Apply hi-res scaling to offsets too
        offset_x = sprite.offset_x * hires_scale * self.trim_shift_multiplier
        offset_y = sprite.offset_y * hires_scale * self.trim_shift_multiplier
        
        # The sprite is drawn at the origin, but trimmed sprites need offset adjustment
        # The offset tells us where the trimmed sprite sits within the original canvas
        sprite_x = offset_x + anchor_dx
        sprite_y = offset_y + anchor_dy
        
        # Scale everything by position_scale for consistent sizing
        sprite_x *= self.position_scale
        sprite_y *= self.position_scale
        sprite_w *= self.position_scale
        sprite_h *= self.position_scale
        
        # Draw the sprite quad
        x1 = sprite_x
        y1 = sprite_y
        x2 = x1 + sprite_w
        y2 = y1 + sprite_h
        if sprite.rotated:
            vertices = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
            texcoords = [(tx2, ty1), (tx2, ty2), (tx1, ty2), (tx1, ty1)]
        else:
            vertices = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
            texcoords = [(tx1, ty1), (tx2, ty1), (tx2, ty2), (tx1, ty2)]
        if render:
            glBegin(GL_QUADS)
            for (u, v), (vx, vy) in zip(texcoords, vertices):
                glTexCoord2f(u, v)
                glVertex2f(vx, vy)
            glEnd()
        quad_triangles = [0, 1, 2, 0, 2, 3]
        return SpriteDrawInfo(vertices, texcoords, quad_triangles, (r, g, b, opacity), sprite, atlas)
    
    def is_point_in_layer(
        self,
        world_x: float,
        world_y: float,
        layer: LayerData,
        world_state: Dict,
        atlases: List[TextureAtlas],
        layer_offsets: Dict[int, Tuple[float, float]]
    ) -> bool:
        """
        Check if a point is inside a layer's sprite bounds using matrix inverse
        Used for interactive sprite dragging
        
        This must match exactly how render_sprite draws the sprite:
        - Sprite is drawn at (offset_x, offset_y) with size (sprite_w, sprite_h)
        - All values are scaled by position_scale and hires_scale
        
        Args:
            world_x: X coordinate in world space
            world_y: Y coordinate in world space
            layer: Layer to check
            world_state: Pre-calculated world state for the layer
            atlases: List of texture atlases
            layer_offsets: User-applied offsets
        
        Returns:
            True if point is inside layer bounds
        """
        # Get sprite info
        sprite_name = world_state['sprite_name']
        if not sprite_name:
            return False
        
        sprite, atlas, _ = self._find_sprite_in_atlases(sprite_name, atlases)
        if not sprite or not atlas:
            return False
        
        # Transform point from world space to local space using inverse matrix
        # Account for user offset (applied before the matrix in render_layer)
        user_offset_x, user_offset_y = layer_offsets.get(layer.layer_id, (0, 0))
        adjusted_x = world_x - user_offset_x
        adjusted_y = world_y - user_offset_y
        
        # Get world matrix components
        m00 = world_state['m00']
        m01 = world_state['m01']
        m10 = world_state['m10']
        m11 = world_state['m11']
        tx = world_state['tx']
        ty = world_state['ty']
        
        # Calculate inverse matrix determinant
        det = m00 * m11 - m01 * m10
        if abs(det) < 1e-10:
            return False  # Matrix is singular, can't invert
        
        # Inverse 2x3 matrix
        inv_m00 = m11 / det
        inv_m01 = -m01 / det
        inv_m10 = -m10 / det
        inv_m11 = m00 / det
        inv_tx = -(inv_m00 * tx + inv_m01 * ty)
        inv_ty = -(inv_m10 * tx + inv_m11 * ty)
        
        # Transform point to local space
        local_x = inv_m00 * adjusted_x + inv_m01 * adjusted_y + inv_tx
        local_y = inv_m10 * adjusted_x + inv_m11 * adjusted_y + inv_ty

        geometry = self._build_polygon_geometry(sprite, atlas)
        if geometry:
            vertices, _, triangles = geometry
            vert_count = len(vertices)
            for i in range(0, len(triangles), 3):
                idx0 = triangles[i]
                idx1 = triangles[i + 1] if i + 1 < len(triangles) else None
                idx2 = triangles[i + 2] if i + 2 < len(triangles) else None
                if idx1 is None or idx2 is None:
                    break
                if idx0 >= vert_count or idx1 >= vert_count or idx2 >= vert_count:
                    continue
                if self._point_in_triangle(
                    local_x, local_y,
                    vertices[idx0], vertices[idx1], vertices[idx2]
                ):
                    return True
            return False
        
        # Get sprite dimensions - must match render_sprite exactly
        if sprite.rotated:
            sprite_w = sprite.h  # After un-rotating
            sprite_h = sprite.w
        else:
            sprite_w = sprite.w
            sprite_h = sprite.h
        
        # Apply hi-res scaling (same as render_sprite)
        hires_scale = 0.5 if atlas.is_hires else 1.0
        sprite_w *= hires_scale
        sprite_h *= hires_scale
        
        # Apply hi-res scaling to offsets (same as render_sprite)
        offset_x = sprite.offset_x * hires_scale * self.trim_shift_multiplier
        offset_y = sprite.offset_y * hires_scale * self.trim_shift_multiplier
        
        # The sprite is drawn at (offset_x, offset_y) in local space
        # Scale everything by position_scale (same as render_sprite)
        sprite_x = offset_x * self.position_scale
        sprite_y = offset_y * self.position_scale
        sprite_w *= self.position_scale
        sprite_h *= self.position_scale
        
        # Check if point is in sprite bounds (matching render_sprite's quad)
        # x1 = sprite_x, y1 = sprite_y, x2 = sprite_x + sprite_w, y2 = sprite_y + sprite_h
        if (sprite_x <= local_x <= sprite_x + sprite_w and
            sprite_y <= local_y <= sprite_y + sprite_h):
            return True
        
        return False
    
    def write_log_to_file(self, filename: str = "sprite_positions_LAYER_ANCHOR.txt"):
        """Write collected log data to file"""
        if not self.log_data:
            print("No log data to write")
            return
        
        with open(filename, 'w') as f:
            f.write("=== SPRITE POSITION LOG (LAYER ANCHOR VERSION) ===\n")
            f.write(f"Total layers: {len(self.log_data)}\n\n")
            
            for entry in self.log_data:
                f.write(f"Layer: {entry['layer']} (ID {entry['layer_id']})\n")
                f.write(f"  Original Anchor: {entry['original_anchor']}\n")
                f.write(f"  Layer Anchor: {entry['layer_anchor']}\n")
                f.write(f"  Position: ({entry['position'][0]:.2f}, {entry['position'][1]:.2f})\n")
                f.write(f"  Rotation: {entry['rotation']:.2f}°\n")
                f.write(f"  Scale: ({entry['scale'][0]:.3f}, {entry['scale'][1]:.3f})\n")
                pivot_info = entry.get('pivot_adjustment')
                if pivot_info:
                    f.write("  Pivot Adjustment:\n")
                    f.write(f"    Sprite: {pivot_info.get('sprite')}\n")
                    resolved = pivot_info.get('resolved_sprite')
                    if resolved and resolved != pivot_info.get('sprite'):
                        f.write(f"    Resolved Sprite: {resolved}\n")
                    f.write(f"    Pivot: {pivot_info.get('pivot')}\n")
                    f.write(f"    Sprite Size: {pivot_info.get('sprite_size')}\n")
                    f.write(f"    Original Anchor: {pivot_info.get('original_anchor')}\n")
                    f.write(f"    Adjusted Anchor: {pivot_info.get('adjusted_anchor')}\n")
                f.write("\n")
        
        print(f"Log written to {filename} ({len(self.log_data)} layers)")
        self.log_data.clear()

    def _get_shader_preset(self, shader_name: Optional[str]) -> Optional[ShaderPreset]:
        """Return a preset describing how to approximate a shader."""
        if not shader_name or not self.shader_registry:
            return None
        preset = self.shader_registry.get_preset(shader_name)
        if not preset and shader_name not in self._missing_shader_warnings:
            print(f"[Shader] No preset defined for '{shader_name}'.")
            self._missing_shader_warnings.add(shader_name)
        return preset

    def _get_shader_behavior(self, shader_name: Optional[str]) -> Optional[ShaderBehavior]:
        if not shader_name or not self.shader_registry:
            return None
        return self.shader_registry.get_behavior(shader_name)

    @staticmethod
    def _blend_value_from_preset(preset: Optional[ShaderPreset]) -> Optional[int]:
        """Resolve blend override string to BlendMode value."""
        if not preset or not preset.blend_mode:
            return None
        name = str(preset.blend_mode).upper()
        mapping = {
            "STANDARD": BlendMode.STANDARD,
            "PREMULT_ALPHA": BlendMode.PREMULT_ALPHA,
            "PREMULT_ALPHA_ALT": BlendMode.PREMULT_ALPHA_ALT,
            "PREMULT_ALPHA_ALT2": BlendMode.PREMULT_ALPHA_ALT2,
            "ADDITIVE": BlendMode.ADDITIVE,
            "MULTIPLY": BlendMode.MULTIPLY,
            "SCREEN": BlendMode.SCREEN,
            "INHERIT": BlendMode.INHERIT,
        }
        return mapping.get(name)

    def set_shader_registry(self, registry: Optional[ShaderRegistry]):
        """Inject the shader registry so presets can be resolved at runtime."""
        self.shader_registry = registry
        self._missing_shader_warnings.clear()
        self.shader_texture_cache.clear()

    def set_costume_pivot_adjustment_enabled(self, enabled: bool):
        """Toggle whether costume sprite pivots override layer anchors."""
        self.costume_pivot_adjustment_enabled = bool(enabled)

    def _get_shader_texture(self, path: Optional[str]) -> Optional[ShaderOverlayTexture]:
        if not path:
            return None
        overlay = self.shader_texture_cache.get(path)
        if not overlay:
            overlay = ShaderOverlayTexture(path=path)
            self.shader_texture_cache[path] = overlay
        if overlay.ensure_loaded():
            return overlay
        return None

    def _render_shader_overlay(
        self,
        draw_info: Optional[SpriteDrawInfo],
        behavior: ShaderBehavior,
        preset: Optional[ShaderPreset]
    ):
        if not draw_info:
            return
        metadata = (preset.metadata if preset else {}) or {}
        texture_path = metadata.get("sequence_texture") or metadata.get("lut")
        if not texture_path:
            return
        overlay = self._get_shader_texture(texture_path)
        if not overlay or not overlay.texture_id:
            return
        frame = behavior.compute_frame(self.current_time)
        texture_size = (overlay.width, overlay.height)
        transformed_uvs = self._compute_overlay_uvs(draw_info, behavior, frame, texture_size)
        stencil_active = self._apply_overlay_stencil(draw_info)
        try:
            glBindTexture(GL_TEXTURE_2D, overlay.texture_id)
            glColor4f(*draw_info.color)
            triangles = draw_info.triangles or []
            if triangles:
                glBegin(GL_TRIANGLES)
                vert_count = len(draw_info.vertices)
                for i in range(0, len(triangles), 3):
                    idx0 = triangles[i]
                    idx1 = triangles[i + 1] if i + 1 < len(triangles) else None
                    idx2 = triangles[i + 2] if i + 2 < len(triangles) else None
                    if idx1 is None or idx2 is None:
                        break
                    if idx0 >= vert_count or idx1 >= vert_count or idx2 >= vert_count:
                        continue
                    for idx in (idx0, idx1, idx2):
                        u, v = transformed_uvs[idx]
                        vx, vy = draw_info.vertices[idx]
                        glTexCoord2f(u, v)
                        glVertex2f(vx, vy)
                glEnd()
            else:
                primitive = GL_QUADS if len(draw_info.vertices) == 4 else GL_TRIANGLE_FAN
                glBegin(primitive)
                for (vx, vy), (u, v) in zip(draw_info.vertices, transformed_uvs):
                    glTexCoord2f(u, v)
                    glVertex2f(vx, vy)
                glEnd()
        finally:
            self._restore_overlay_stencil(stencil_active)

    def _compute_overlay_uvs(
        self,
        draw_info: SpriteDrawInfo,
        behavior: ShaderBehavior,
        frame_index: float,
        texture_size: Tuple[int, int]
    ) -> List[Tuple[float, float]]:
        sprite = draw_info.sprite
        atlas = draw_info.atlas
        mode = (behavior.mapping_mode or "sprite").lower()
        if mode in ("sheet", "atlas", "projected"):
            frame = frame_index
            return [
                behavior.transform_uv(texcoord, frame, texture_size)
                for texcoord in draw_info.texcoords
            ]
        if (
            mode in ("sprite", "strip", "trimmed", "auto")
            and sprite
            and atlas
            and not getattr(sprite, "rotated", False)
            and atlas.image_width
            and atlas.image_height
            and sprite.w
            and sprite.h
        ):
            return self._sprite_aligned_overlay_uvs(
                draw_info,
                behavior,
                frame_index,
                atlas.image_width,
                atlas.image_height,
            )
        frame = frame_index
        return [
            behavior.transform_uv(texcoord, frame, texture_size)
            for texcoord in draw_info.texcoords
        ]

    def _sprite_aligned_overlay_uvs(
        self,
        draw_info: SpriteDrawInfo,
        behavior: ShaderBehavior,
        frame_index: float,
        atlas_width: int,
        atlas_height: int
    ) -> List[Tuple[float, float]]:
        sprite = draw_info.sprite
        if not sprite:
            return [(0.0, 0.0) for _ in draw_info.texcoords]
        sprite_w = max(1.0, float(sprite.w))
        sprite_h = max(1.0, float(sprite.h))
        start_x = float(sprite.x)
        start_y = float(sprite.y)
        frame_total = max(1, behavior.frame_count())
        frame_axis = (behavior.frame_axis or "u").lower()
        slot = int(round(frame_index))
        slot = max(0, min(frame_total - 1, slot))
        step = 1.0 / frame_total

        coords: List[Tuple[float, float]] = []
        for tex_u, tex_v in draw_info.texcoords:
            px = tex_u * atlas_width
            py = tex_v * atlas_height
            local_x = (px - start_x) / sprite_w
            local_y = (py - start_y) / sprite_h
            local_x = max(0.0, min(1.0, local_x))
            local_y = max(0.0, min(1.0, local_y))
            if frame_axis == "v":
                u = local_x
                v = slot * step + local_y * step
            else:
                u = slot * step + local_x * step
                v = local_y
            coords.append((max(0.0, min(1.0, u)), max(0.0, min(1.0, v))))
        return coords

    def _apply_overlay_stencil(self, draw_info: SpriteDrawInfo) -> bool:
        """Mask overlay rendering to the base sprite shape using the stencil buffer."""
        vertices = draw_info.vertices
        if not vertices:
            return False
        glPushAttrib(GL_STENCIL_BUFFER_BIT | GL_COLOR_BUFFER_BIT | GL_ENABLE_BIT)
        glEnable(GL_STENCIL_TEST)
        glClearStencil(0)
        glClear(GL_STENCIL_BUFFER_BIT)
        glStencilFunc(GL_ALWAYS, 1, 0xFF)
        glStencilOp(GL_REPLACE, GL_REPLACE, GL_REPLACE)
        glColorMask(GL_FALSE, GL_FALSE, GL_FALSE, GL_FALSE)
        glDisable(GL_TEXTURE_2D)
        self._draw_overlay_geometry(vertices, draw_info.triangles)
        glEnable(GL_TEXTURE_2D)
        glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE)
        glStencilFunc(GL_EQUAL, 1, 0xFF)
        glStencilOp(GL_KEEP, GL_KEEP, GL_KEEP)
        return True

    def _restore_overlay_stencil(self, active: bool) -> None:
        if not active:
            return
        glDisable(GL_STENCIL_TEST)
        glPopAttrib()

    def _draw_overlay_geometry(self, vertices: List[Tuple[float, float]], triangles: List[int]) -> None:
        if triangles:
            glBegin(GL_TRIANGLES)
            vert_count = len(vertices)
            for i in range(0, len(triangles), 3):
                idx0 = triangles[i]
                idx1 = triangles[i + 1] if i + 1 < len(triangles) else None
                idx2 = triangles[i + 2] if i + 2 < len(triangles) else None
                if idx1 is None or idx2 is None:
                    break
                if idx0 >= vert_count or idx1 >= vert_count or idx2 >= vert_count:
                    continue
                x0, y0 = vertices[idx0]
                x1, y1 = vertices[idx1]
                x2, y2 = vertices[idx2]
                glVertex2f(x0, y0)
                glVertex2f(x1, y1)
                glVertex2f(x2, y2)
            glEnd()
            return
        primitive = GL_QUADS if len(vertices) == 4 else GL_TRIANGLE_FAN
        glBegin(primitive)
        for vx, vy in vertices:
            glVertex2f(vx, vy)
        glEnd()
        glColor4f(*draw_info.color)
        triangles = draw_info.triangles or []
        if triangles:
            glBegin(GL_TRIANGLES)
            vert_count = len(draw_info.vertices)
            for i in range(0, len(triangles), 3):
                idx0 = triangles[i]
                idx1 = triangles[i + 1] if i + 1 < len(triangles) else None
                idx2 = triangles[i + 2] if i + 2 < len(triangles) else None
                if idx1 is None or idx2 is None:
                    break
                if idx0 >= vert_count or idx1 >= vert_count or idx2 >= vert_count:
                    continue
                self._emit_overlay_vertex(
                    idx0, draw_info.vertices, transformed_uvs, base_texcoords, use_mask
                )
                self._emit_overlay_vertex(
                    idx1, draw_info.vertices, transformed_uvs, base_texcoords, use_mask
                )
                self._emit_overlay_vertex(
                    idx2, draw_info.vertices, transformed_uvs, base_texcoords, use_mask
                )
            glEnd()
        else:
            primitive = GL_QUADS if len(draw_info.vertices) == 4 else GL_TRIANGLE_FAN
            glBegin(primitive)
            for idx in range(len(draw_info.vertices)):
                self._emit_overlay_vertex(
                    idx, draw_info.vertices, transformed_uvs, base_texcoords, use_mask
                )
            glEnd()
        self._end_overlay_textures(use_mask)

    def _render_mask_source_layer(
        self,
        layer: LayerData,
        world_state: Dict,
        atlases: List[TextureAtlas],
        layer_offsets: Dict[int, Tuple[float, float]],
        mask_key: str
    ) -> None:
        glPushMatrix()
        offset_x, offset_y = layer_offsets.get(layer.layer_id, (0, 0))
        if offset_x or offset_y:
            glTranslatef(offset_x, offset_y, 0)
        matrix = [
            world_state['m00'], world_state['m10'], 0.0, 0.0,
            world_state['m01'], world_state['m11'], 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            world_state['tx'], world_state['ty'], 0.0, 1.0
        ]
        glMultMatrixf(matrix)
        mask_written = self._write_mask_to_stencil(world_state, atlases, layer)
        glPopMatrix()
        reset_blend_mode()
        if mask_written:
            self.pending_mask_key = mask_key
        else:
            self.pending_mask_key = None
            self._log_mask_warning(
                f"{mask_key}:source",
                f"Failed to capture mask geometry for layer '{layer.name}' (key {mask_key})."
            )

    def _write_mask_to_stencil(
        self,
        state: Dict,
        atlases: List[TextureAtlas],
        layer: LayerData
    ) -> bool:
        glPushAttrib(GL_STENCIL_BUFFER_BIT | GL_COLOR_BUFFER_BIT | GL_ENABLE_BIT)
        try:
            glEnable(GL_STENCIL_TEST)
            glClearStencil(0)
            glClear(GL_STENCIL_BUFFER_BIT)
            glStencilFunc(GL_ALWAYS, 1, 0xFF)
            glStencilOp(GL_REPLACE, GL_REPLACE, GL_REPLACE)
            glColorMask(GL_FALSE, GL_FALSE, GL_FALSE, GL_FALSE)
            draw_info = self.render_sprite(
                state,
                state.get('world_opacity', 1.0),
                atlases,
                layer,
                render=True
            )
            return draw_info is not None
        finally:
            glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE)
            glDisable(GL_STENCIL_TEST)
            glPopAttrib()

    def _activate_mask_consumer(self, mask_key: str) -> bool:
        if self.pending_mask_key != mask_key:
            self._log_mask_warning(
                f"{mask_key}:missing",
                f"Mask '{mask_key}' was not initialized before its consumer layer rendered."
            )
            return False
        glPushAttrib(GL_STENCIL_BUFFER_BIT | GL_ENABLE_BIT)
        glEnable(GL_STENCIL_TEST)
        glStencilFunc(GL_EQUAL, 0, 0xFF)
        glStencilOp(GL_KEEP, GL_KEEP, GL_KEEP)
        return True

    def _deactivate_mask_consumer(self, active: bool) -> None:
        if not active:
            return
        glDisable(GL_STENCIL_TEST)
        glPopAttrib()
        self.pending_mask_key = None

    def _log_mask_warning(self, key: str, message: str) -> None:
        if key in self._mask_warning_messages:
            return
        self._mask_warning_messages.add(key)
        print(f"[Mask] {message}")

    def _emit_overlay_vertex(
        self,
        index: int,
        vertices: List[Tuple[float, float]],
        overlay_uvs: List[Tuple[float, float]],
        base_uvs: List[Tuple[float, float]],
        use_mask: bool
    ) -> None:
        u, v = overlay_uvs[index]
        vx, vy = vertices[index]
        glMultiTexCoord2f(GL_TEXTURE0, u, v)
        if use_mask:
            bu, bv = base_uvs[index]
            glMultiTexCoord2f(GL_TEXTURE1, bu, bv)
        glVertex2f(vx, vy)

    def _begin_overlay_textures(self, overlay_id: int, mask_id: Optional[int]) -> None:
        glActiveTexture(GL_TEXTURE0)
        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, overlay_id)
        glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
        if mask_id:
            glActiveTexture(GL_TEXTURE1)
            glEnable(GL_TEXTURE_2D)
            glBindTexture(GL_TEXTURE_2D, mask_id)
            glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_COMBINE)
            glTexEnvi(GL_TEXTURE_ENV, GL_COMBINE_RGB, GL_MODULATE)
            glTexEnvi(GL_TEXTURE_ENV, GL_SOURCE0_RGB, GL_PREVIOUS)
            glTexEnvi(GL_TEXTURE_ENV, GL_OPERAND0_RGB, GL_SRC_COLOR)
            glTexEnvi(GL_TEXTURE_ENV, GL_SOURCE1_RGB, GL_TEXTURE)
            glTexEnvi(GL_TEXTURE_ENV, GL_OPERAND1_RGB, GL_SRC_ALPHA)
            glTexEnvi(GL_TEXTURE_ENV, GL_COMBINE_ALPHA, GL_REPLACE)
            glTexEnvi(GL_TEXTURE_ENV, GL_SOURCE0_ALPHA, GL_TEXTURE)
            glTexEnvi(GL_TEXTURE_ENV, GL_OPERAND0_ALPHA, GL_SRC_ALPHA)
        glActiveTexture(GL_TEXTURE0)

    def _end_overlay_textures(self, used_mask: bool) -> None:
        if used_mask:
            glActiveTexture(GL_TEXTURE1)
            glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
            glBindTexture(GL_TEXTURE_2D, 0)
            glDisable(GL_TEXTURE_2D)
            glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, 0)
