"""
Texture Atlas management
Handles loading and managing sprite atlases from XML files
"""

import os
import xml.etree.ElementTree as ET
from typing import Dict, Optional, Tuple
from PIL import Image, UnidentifiedImageError
import numpy as np
from OpenGL.GL import *

try:
    # pillow-heif registers AVIF/HEIF loaders without requiring the user to
    # build Pillow with AVIF support.
    from pillow_heif import open_heif, register_heif_opener

    register_heif_opener()
    _heif_available_error: Optional[str] = None
except Exception as heif_exc:  # pragma: no cover - optional dependency
    print(f"Warning: Failed to enable HEIF/AVIF support: {heif_exc}")
    open_heif = None
    _heif_available_error = str(heif_exc)

try:  # pragma: no cover - optional dependency
    # Importing pillow_avif registers its Pillow plugin automatically.
    import pillow_avif as _pillow_avif_module  # type: ignore  # noqa: F401

    _avif_plugin_error: Optional[str] = None
except Exception as avif_exc:
    _pillow_avif_module = None  # type: ignore
    _avif_plugin_error = str(avif_exc)

HEIF_EXTENSIONS = ('.avif', '.avifs', '.heif', '.heic')

from .data_structures import SpriteInfo


class TextureAtlas:
    """Manages texture atlas loading and sprite information"""
    
    def __init__(self):
        self.texture_id: Optional[int] = None
        self.sprites: Dict[str, SpriteInfo] = {}
        self.image_width: int = 0
        self.image_height: int = 0
        self.logical_width: int = 0
        self.logical_height: int = 0
        self.image_path: str = ""
        self.is_hires: bool = False  # Track if this is a hi-res atlas
        self.source_name: Optional[str] = None  # filename/alias for sheet remap lookups
        self.xml_path: Optional[str] = None  # Source XML path for export/regeneration
        self.source_id: Optional[int] = None  # Numeric source id from animation JSON
    
    def load_from_xml(self, xml_path: str, data_root: str) -> bool:
        """
        Load texture atlas from XML file
        
        Args:
            xml_path: Path to the XML file
            data_root: Root directory for data files
        
        Returns:
            True if successful, False otherwise
        """
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            self.xml_path = xml_path
            def parse_float_pairs(element):
                if element is None or not element.text:
                    return []
                values = []
                for part in element.text.split():
                    try:
                        values.append(float(part))
                    except ValueError:
                        return []
                if len(values) % 2 != 0:
                    return []
                return [
                    (values[i], values[i + 1])
                    for i in range(0, len(values), 2)
                ]

            def parse_int_list(element):
                if element is None or not element.text:
                    return []
                ints = []
                for part in element.text.split():
                    try:
                        ints.append(int(part))
                    except ValueError:
                        return []
                return ints
            
            # Get image path from XML
            image_path = root.get('imagePath', '')
            if not image_path:
                return False
            
            # Check if this is a hi-res atlas (sprites are 2x size, need 0.5x scale)
            self.is_hires = root.get('hires', '').lower() == 'true'
            
            # Try both .png and .avif extensions
            full_image_path = os.path.join(data_root, image_path)
            if not os.path.exists(full_image_path):
                # Try .avif extension
                avif_path = os.path.splitext(full_image_path)[0] + '.avif'
                if os.path.exists(avif_path):
                    full_image_path = avif_path
                else:
                    # Try without extension change (might already be .avif in XML)
                    return False
            
            self.image_path = full_image_path
            declared_width = int(root.get('width', 0))
            declared_height = int(root.get('height', 0))
            self.logical_width = declared_width
            self.logical_height = declared_height
            actual_width, actual_height = self._probe_image_size(full_image_path)
            self.image_width = actual_width or declared_width
            self.image_height = actual_height or declared_height
            
            duplicate_names = set()
            # Parse sprites
            for sprite_elem in root.findall('sprite'):
                name = sprite_elem.get('n', '')
                if not name:
                    continue
                if name in self.sprites:
                    duplicate_names.add(name)
                    continue
                raw_vertices = parse_float_pairs(sprite_elem.find('vertices'))
                raw_vertices_uv = parse_float_pairs(sprite_elem.find('verticesUV'))
                triangles = parse_int_list(sprite_elem.find('triangles'))
                vertices = raw_vertices if raw_vertices else []
                vertices_uv = []
                if (
                    raw_vertices
                    and raw_vertices_uv
                    and triangles
                    and len(raw_vertices) == len(raw_vertices_uv)
                    and self.image_width > 0
                    and self.image_height > 0
                ):
                    inv_w = 1.0 / self.image_width
                    inv_h = 1.0 / self.image_height
                    vertices_uv = [
                        (uv_x * inv_w, uv_y * inv_h)
                        for uv_x, uv_y in raw_vertices_uv
                    ]
                else:
                    triangles = []
                    vertices = []
                    vertices_uv = []
                sprite = SpriteInfo(
                    name=name,
                    x=int(sprite_elem.get('x', 0)),
                    y=int(sprite_elem.get('y', 0)),
                    w=int(sprite_elem.get('w', 0)),
                    h=int(sprite_elem.get('h', 0)),
                    pivot_x=float(sprite_elem.get('pX', 0.5)),
                    pivot_y=float(sprite_elem.get('pY', 0.5)),
                    offset_x=float(sprite_elem.get('oX', 0) or 0),
                    offset_y=float(sprite_elem.get('oY', 0) or 0),
                    original_w=float(sprite_elem.get('oW', 0) or 0),
                    original_h=float(sprite_elem.get('oH', 0) or 0),
                    rotated=sprite_elem.get('r', '') == 'y',
                    vertices=vertices,
                    vertices_uv=vertices_uv,
                    triangles=triangles
                )
                
                # Use original dimensions if specified, otherwise use sprite dimensions
                # For rotated sprites, the original dimensions are swapped relative to atlas dimensions
                if sprite.original_w == 0:
                    sprite.original_w = sprite.h if sprite.rotated else sprite.w
                if sprite.original_h == 0:
                    sprite.original_h = sprite.w if sprite.rotated else sprite.h
                
                # Calculate derived values (matching game's FUN_005862a0)
                # These represent the remaining trimmed space on right/bottom:
                # derived_w = oW - oX - w, derived_h = oH - oY - h (swap w/h when rotated).
                if sprite.original_w > 0 and sprite.original_h > 0:
                    if sprite.rotated:
                        sprite.derived_w = sprite.original_w - sprite.offset_y - sprite.h
                        sprite.derived_h = sprite.original_h - sprite.offset_x - sprite.w
                    else:
                        sprite.derived_w = sprite.original_w - sprite.offset_x - sprite.w
                        sprite.derived_h = sprite.original_h - sprite.offset_y - sprite.h
                else:
                    sprite.derived_w = 0.0
                    sprite.derived_h = 0.0
                
                self.sprites[sprite.name] = sprite

            if duplicate_names:
                print(f"Warning: duplicate sprite names skipped in {os.path.basename(xml_path)}: "
                      f"{', '.join(sorted(duplicate_names))}")
            
            return True
        except Exception as e:
            print(f"Error loading texture atlas: {e}")
            return False

    @staticmethod
    def _probe_image_size(image_path: str) -> Tuple[int, int]:
        """Return the on-disk pixel dimensions for the atlas texture."""
        try:
            with Image.open(image_path) as img:
                return img.width, img.height
        except Exception:
            return 0, 0
    
    def load_texture(self) -> bool:
        """
        Load the texture into OpenGL with premultiplied alpha
        
        The MSM game engine uses premultiplied alpha blending:
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
        
        This requires textures where RGB values are pre-multiplied by alpha:
        R' = R * A, G' = G * A, B' = B * A
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Load image using PIL
            img = self._load_texture_image(self.image_path)
            img = img.convert('RGBA')
            img_data = np.array(img, dtype=np.float32) / 255.0
            
            # Premultiply alpha: RGB = RGB * A
            # This is required for proper blending with GL_ONE, GL_ONE_MINUS_SRC_ALPHA
            alpha = img_data[:, :, 3:4]  # Keep as 3D array for broadcasting
            img_data[:, :, 0:3] *= alpha
            
            # Convert back to uint8
            img_data = (img_data * 255.0).astype(np.uint8)
            
            # Generate OpenGL texture
            self.texture_id = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, self.texture_id)
            
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, img.width, img.height,
                        0, GL_RGBA, GL_UNSIGNED_BYTE, img_data)
            
            return True
        except Exception as e:
            print(f"Error loading texture: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _load_texture_image(self, image_path: str) -> Image.Image:
        """
        Load the underlying spritesheet, falling back to pillow-heif when
        Pillow's native decoders cannot parse AVIF/HEIF sources end users
        extracted from the game.
        """
        suffix = os.path.splitext(image_path)[1].lower()
        errors = []
        try:
            return Image.open(image_path)
        except UnidentifiedImageError as pil_exc:
            if suffix not in HEIF_EXTENSIONS:
                raise
            errors.append(f"Pillow: {pil_exc}")

        heif_image = self._decode_with_pillow_heif(image_path, errors)
        if heif_image:
            return heif_image

        avif_image = self._decode_with_avif_plugin(image_path, errors)
        if avif_image:
            return avif_image

        raise RuntimeError(
            "Failed to decode HEIF/AVIF texture "
            f"'{os.path.basename(image_path)}'. Tried decoders:\n- "
            + "\n- ".join(errors)
        )

    def _decode_with_pillow_heif(self, image_path: str, errors: list[str]) -> Optional[Image.Image]:
        if open_heif is None:
            extra = f" ({_heif_available_error})" if _heif_available_error else ""
            errors.append(f"pillow-heif unavailable{extra or ''}")
            return None
        try:
            heif_file = open_heif(image_path, convert_hdr_to_8bit=False)
            return heif_file.to_pillow()
        except Exception as heif_exc:  # pragma: no cover - runtime-only path
            errors.append(f"pillow-heif: {heif_exc}")
            return None

    def _decode_with_avif_plugin(self, image_path: str, errors: list[str]) -> Optional[Image.Image]:
        if _pillow_avif_module is None:  # type: ignore
            extra = f" ({_avif_plugin_error})" if _avif_plugin_error else ""
            errors.append(f"pillow-avif-plugin unavailable{extra or ''}")
            return None
        try:
            return Image.open(image_path)
        except UnidentifiedImageError as avif_exc:
            errors.append(f"pillow-avif-plugin: {avif_exc}")
            return None

    def get_sprite(self, name: str) -> Optional[SpriteInfo]:
        """
        Get sprite information by name
        
        Args:
            name: Name of the sprite
        
        Returns:
            SpriteInfo if found, None otherwise
        """
        return self.sprites.get(name)
