"""
Animation Player
Handles animation playback, timing, and keyframe interpolation
"""

import re
from typing import Optional, Dict
from .data_structures import AnimationData, LayerData, KeyframeData


class AnimationPlayer:
    """Handles animation playback and interpolation"""
    
    _sprite_suffix_pattern = re.compile(r'^(.*?)(\d+)$')
    
    def __init__(self):
        self.animation: Optional[AnimationData] = None
        self.current_time: float = 0.0
        self.playing: bool = False
        self.loop: bool = True
        self.duration: float = 0.0
        self.playback_speed: float = 1.0
        # Whether linear interpolation (tweening) is enabled.
        # When False, values snap to the previous keyframe (no tween).
        self.tweening_enabled: bool = True
    
    def load_animation(self, anim_data: AnimationData):
        """
        Load animation data
        
        Args:
            anim_data: Animation data to load
        """
        self.animation = anim_data
        self.current_time = 0.0
        self.calculate_duration()
    
    def calculate_duration(self):
        """Calculate animation duration from keyframes"""
        if not self.animation:
            self.duration = 0.0
            return
        
        max_time = 0.0
        for layer in self.animation.layers:
            if layer.keyframes:
                last_keyframe = max(layer.keyframes, key=lambda k: k.time)
                max_time = max(max_time, last_keyframe.time)
        
        self.duration = max_time
    
    def update(self, delta_time: float):
        """
        Update animation time
        
        Args:
            delta_time: Time elapsed since last update (in seconds)
        """
        if not self.playing or not self.animation:
            return
        
        speed = max(0.01, self.playback_speed)
        self.current_time += delta_time * speed
        
        if self.current_time > self.duration:
            if self.loop:
                self.current_time = 0.0
            else:
                self.current_time = self.duration
                self.playing = False
    
    def get_layer_state(self, layer: LayerData, time: float) -> Dict:
        """
        Get interpolated layer state at given time
        
        Args:
            layer: Layer to get state for
            time: Time to get state at
        
        Returns:
            Dictionary containing interpolated state values
        """
        if not layer.keyframes:
            return {
                'pos_x': 0, 'pos_y': 0,
                'scale_x': 100, 'scale_y': 100,
                'rotation': 0, 'opacity': 100,
                'sprite_name': '', 'r': 255, 'g': 255, 'b': 255
            }
        
        # Helper function to get value at time with interpolation
        def get_value_at_time(keyframes, attr_name, immediate_attr, default_val):
            # Filter keyframes that have this attribute set (immediate != -1)
            valid_kfs = [(kf.time, getattr(kf, attr_name), getattr(kf, immediate_attr)) 
                         for kf in keyframes if getattr(kf, immediate_attr) != -1]
            
            if not valid_kfs:
                return default_val
            
            # Find the last keyframe at or before current time
            prev_kf = None
            for kf_time, kf_val, kf_interp in valid_kfs:
                if kf_time <= time:
                    prev_kf = (kf_time, kf_val, kf_interp)
            
            if prev_kf is None:
                return valid_kfs[0][1]  # Return first keyframe value
            
            # If interpolation is NONE (1) or we're at/past the last keyframe, return current value
            if prev_kf[2] == 1:  # NONE interpolation
                return prev_kf[1]
            
            # Find next keyframe for interpolation
            next_kf = None
            for kf_time, kf_val, kf_interp in valid_kfs:
                if kf_time > time:
                    next_kf = (kf_time, kf_val, kf_interp)
                    break
            
            if next_kf is None:
                return prev_kf[1]  # No next keyframe, return current
            
            # LINEAR interpolation (0)
            if prev_kf[2] == 0:
                # If tweening has been disabled globally, treat linear interpolation
                # as immediate (snap to prev keyframe) instead of interpolating.
                if not getattr(self, "tweening_enabled", True):
                    return prev_kf[1]
                time_diff = next_kf[0] - prev_kf[0]
                if time_diff > 0:
                    t = (time - prev_kf[0]) / time_diff
                    return self.lerp(prev_kf[1], next_kf[1], t)
            
            return prev_kf[1]
        
        # Get position
        pos_x = get_value_at_time(layer.keyframes, 'pos_x', 'immediate_pos', 0)
        pos_y = get_value_at_time(layer.keyframes, 'pos_y', 'immediate_pos', 0)
        
        # Get scale
        scale_x = get_value_at_time(layer.keyframes, 'scale_x', 'immediate_scale', 100)
        scale_y = get_value_at_time(layer.keyframes, 'scale_y', 'immediate_scale', 100)
        
        # Get rotation
        rotation = get_value_at_time(layer.keyframes, 'rotation', 'immediate_rotation', 0)
        
        # Get opacity
        opacity = get_value_at_time(layer.keyframes, 'opacity', 'immediate_opacity', 100)
        
        # Get sprite name with optional numeric interpolation
        sprite_name = ''
        sprite_keyframes = [kf for kf in layer.keyframes if kf.immediate_sprite != -1]
        prev_sprite = None
        next_sprite = None
        for kf in sprite_keyframes:
            if kf.time <= time:
                prev_sprite = kf
            elif kf.time > time:
                next_sprite = kf
                break
        
        if prev_sprite:
            sprite_name = prev_sprite.sprite_name
            if prev_sprite.immediate_sprite == 0 and next_sprite:
                interpolated = self._get_interpolated_sprite_name(prev_sprite, next_sprite, time)
                if interpolated:
                    sprite_name = interpolated
        elif sprite_keyframes:
            sprite_name = sprite_keyframes[0].sprite_name
        
        # Get RGB
        r = int(get_value_at_time(layer.keyframes, 'r', 'immediate_rgb', 255))
        g = int(get_value_at_time(layer.keyframes, 'g', 'immediate_rgb', 255))
        b = int(get_value_at_time(layer.keyframes, 'b', 'immediate_rgb', 255))

        if getattr(layer, "render_tags", None) and "neutral_color" in layer.render_tags:
            r = g = b = 255

        return {
            'pos_x': pos_x, 'pos_y': pos_y,
            'scale_x': scale_x, 'scale_y': scale_y,
            'rotation': rotation, 'opacity': opacity,
            'sprite_name': sprite_name,
            'r': r, 'g': g, 'b': b
        }
    
    @staticmethod
    def lerp(a: float, b: float, t: float) -> float:
        """
        Linear interpolation between two values
        
        Args:
            a: Start value
            b: End value
            t: Interpolation factor (0-1)
        
        Returns:
            Interpolated value
        """
        return a + (b - a) * t
    
    def _get_interpolated_sprite_name(
        self,
        prev_kf: KeyframeData,
        next_kf: KeyframeData,
        time: float
    ) -> Optional[str]:
        """
        Return an interpolated sprite name when keyframes define a numeric range.
        """
        if not prev_kf.sprite_name or not next_kf.sprite_name:
            return None
        if next_kf.time <= prev_kf.time:
            return None
        
        match_prev = self._sprite_suffix_pattern.match(prev_kf.sprite_name)
        match_next = self._sprite_suffix_pattern.match(next_kf.sprite_name)
        if not match_prev or not match_next:
            return None
        if match_prev.group(1) != match_next.group(1):
            return None
        
        start_idx = int(match_prev.group(2))
        end_idx = int(match_next.group(2))
        if start_idx == end_idx:
            return prev_kf.sprite_name
        
        duration = next_kf.time - prev_kf.time
        if duration <= 0:
            return prev_kf.sprite_name
        
        ratio = (time - prev_kf.time) / duration
        ratio = max(0.0, min(0.9999, ratio))
        
        span = end_idx - start_idx
        steps = abs(span)
        if steps == 0:
            return prev_kf.sprite_name
        
        advance = min(steps - 1, int(ratio * steps))
        if span > 0:
            candidate_idx = start_idx + advance
        else:
            candidate_idx = start_idx - advance

        # Preserve leading zeros from the suffix so atlas names continue to match
        suffix_width = len(match_prev.group(2))
        formatted_idx = str(candidate_idx).zfill(suffix_width)
        prefix = match_prev.group(1)
        return f"{prefix}{formatted_idx}"

    def set_playback_speed(self, speed: float):
        """Adjust playback speed multiplier (>0)."""
        if speed <= 0:
            speed = 0.01
        self.playback_speed = speed
