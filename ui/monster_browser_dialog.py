"""
Monster Browser Dialog
Visual picker for monsters using book portrait thumbnails.
Optimized for performance with lazy loading and background thumbnail processing.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple
from concurrent.futures import ThreadPoolExecutor
import threading

from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal, QObject, QRect
from PyQt6.QtGui import QPixmap, QColor, QImage
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QApplication,
)
from PIL import Image


@dataclass
class MonsterVariantOption:
    """Represents an alternate BIN/JSON pair for a monster (e.g., island-specific files)."""

    display_name: str
    relative_path: str
    json_path: Optional[str]
    bin_path: Optional[str]
    variant_label: str
    stem: str

    @property
    def source_path(self) -> Optional[str]:
        return self.json_path or self.bin_path


@dataclass
class MonsterBrowserEntry:
    """Represents a monster file pair and associated portrait."""

    token: str
    display_name: str
    relative_path: str
    image_path: str
    json_path: Optional[str]
    bin_path: Optional[str]
    variants: List[MonsterVariantOption] = field(default_factory=list)
    search_blob: str = field(init=False)

    def __post_init__(self):
        parts = [
            self.token or "",
            self.display_name or "",
            self.relative_path or "",
            (self.json_path or ""),
            (self.bin_path or ""),
        ]
        stems = []
        for path in (self.json_path, self.bin_path):
            if path:
                stems.append(Path(path).stem)
        parts.extend(stems)
        for variant in self.variants:
            parts.extend(
                [
                    variant.display_name,
                    variant.relative_path,
                    variant.variant_label,
                    variant.stem,
                    (variant.json_path or ""),
                    (variant.bin_path or ""),
                ]
            )
        self.search_blob = " ".join(part for part in parts if part).lower()

    def has_variants(self) -> bool:
        return bool(self.variants)


class ThumbnailLoader(QObject):
    """Background thumbnail loader with thread pool."""
    
    thumbnail_ready = pyqtSignal(str, QPixmap)  # image_path, pixmap
    
    def __init__(self, thumb_size: QSize, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._thumb_size = thumb_size
        self._cache: OrderedDict[str, QPixmap] = OrderedDict()
        self._cache_limit = 512
        self._pending: Set[str] = set()
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ThumbLoader")
        self._shutdown = False
        
    def shutdown(self):
        """Clean up the thread pool."""
        self._shutdown = True
        self._executor.shutdown(wait=False, cancel_futures=True)
        
    def get_cached(self, image_path: str) -> Optional[QPixmap]:
        """Get thumbnail from cache if available (thread-safe)."""
        key = self._normalize_path(image_path)
        with self._lock:
            pixmap = self._cache.get(key)
            if pixmap is not None:
                self._cache.move_to_end(key)
            return pixmap
    
    def request_thumbnail(self, image_path: str):
        """Request a thumbnail to be loaded in background."""
        if self._shutdown:
            return
        key = self._normalize_path(image_path)
        with self._lock:
            if key in self._cache or key in self._pending:
                return
            self._pending.add(key)
        self._executor.submit(self._load_thumbnail, image_path, key)
    
    def _normalize_path(self, path: str) -> str:
        return os.path.normcase(os.path.abspath(path)) if path else ""
    
    def _load_thumbnail(self, image_path: str, key: str):
        """Load thumbnail in background thread."""
        if self._shutdown:
            return
        try:
            pixmap = self._load_and_scale(image_path)
            if pixmap and not pixmap.isNull():
                with self._lock:
                    self._cache[key] = pixmap
                    self._pending.discard(key)
                    # Evict old entries
                    while len(self._cache) > self._cache_limit:
                        self._cache.popitem(last=False)
                # Emit signal on main thread
                if not self._shutdown:
                    self.thumbnail_ready.emit(image_path, pixmap)
            else:
                with self._lock:
                    self._pending.discard(key)
        except Exception:
            with self._lock:
                self._pending.discard(key)
    
    def _load_and_scale(self, image_path: str) -> Optional[QPixmap]:
        """Load image and scale to thumbnail size."""
        if not image_path or not os.path.exists(image_path):
            return None
        
        # Try Qt native loading first (faster for common formats)
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            # Fall back to PIL for exotic formats
            pixmap = self._load_via_pillow(image_path)
        
        if pixmap and not pixmap.isNull():
            pixmap = pixmap.scaled(
                self._thumb_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        return pixmap
    
    def _load_via_pillow(self, path: str) -> Optional[QPixmap]:
        """Load image using PIL and convert to QPixmap."""
        try:
            with Image.open(path) as img:
                if img.mode not in ("RGBA", "RGB"):
                    img = img.convert("RGBA")
                # Convert to QImage directly without ImageQt (faster)
                if img.mode == "RGBA":
                    data = img.tobytes("raw", "BGRA")
                    qimage = QImage(data, img.width, img.height, QImage.Format.Format_ARGB32)
                else:
                    data = img.tobytes("raw", "BGR")
                    qimage = QImage(data, img.width, img.height, QImage.Format.Format_RGB888).rgbSwapped()
                # Must copy since data buffer will be freed
                return QPixmap.fromImage(qimage.copy())
        except Exception:
            return None


class MonsterCardWidget(QFrame):
    """Clickable card that shows a monster portrait and name."""

    _placeholder_pixmap: Optional[QPixmap] = None

    def __init__(
        self,
        thumb_size: QSize,
        click_callback: Callable[[MonsterBrowserEntry, Optional[MonsterVariantOption], str], None],
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.entry: Optional[MonsterBrowserEntry] = None
        self.variant_option: Optional[MonsterVariantOption] = None
        self._callback = click_callback
        self._thumb_size = thumb_size
        self._thumbnail_loaded = False
        self._can_expand = False
        self._is_expanded = False
        
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(thumb_size.width() + 20, thumb_size.height() + 90)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setFixedSize(thumb_size)
        layout.addWidget(self.image_label)

        self.name_label = QLabel("")
        self.name_label.setWordWrap(True)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setStyleSheet("font-weight: bold;")
        self.name_label.setMaximumHeight(24)
        layout.addWidget(self.name_label)

        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_label.setStyleSheet("color: gray; font-size: 8pt;")
        self.detail_label.setMaximumHeight(36)
        layout.addWidget(self.detail_label)

        self.variant_hint_label = QLabel("")
        self.variant_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.variant_hint_label.setStyleSheet("color: #ffaa00; font-size: 8pt;")
        self.variant_hint_label.hide()
        layout.addWidget(self.variant_hint_label)

        self.load_button = QPushButton("Load Default")
        self.load_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.load_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.load_button.setVisible(False)
        self.load_button.clicked.connect(self._handle_load_clicked)
        layout.addWidget(self.load_button)
        
        # Set placeholder
        self._set_placeholder()

    @classmethod
    def _get_placeholder(cls, size: QSize) -> QPixmap:
        """Get or create a shared placeholder pixmap."""
        if cls._placeholder_pixmap is None or cls._placeholder_pixmap.size() != size:
            cls._placeholder_pixmap = QPixmap(size)
            cls._placeholder_pixmap.fill(QColor("#333333"))
        return cls._placeholder_pixmap

    def _set_placeholder(self):
        """Set the placeholder image."""
        self.image_label.setPixmap(self._get_placeholder(self._thumb_size))
        self._thumbnail_loaded = False

    def set_entry(
        self,
        entry: MonsterBrowserEntry,
        variant: Optional[MonsterVariantOption] = None,
        *,
        can_expand: bool = False,
        expanded: bool = False,
    ):
        """Update the card with a new entry or variant."""
        self.entry = entry
        self.variant_option = variant
        self._can_expand = bool(can_expand and variant is None)
        self._is_expanded = bool(expanded and self._can_expand)
        self._thumbnail_loaded = False
        self._set_placeholder()

        if variant:
            self.name_label.setText(variant.variant_label or variant.display_name)
            detail = variant.relative_path or variant.display_name or entry.relative_path
            self.detail_label.setText(detail)
            self.variant_hint_label.setText(entry.display_name)
            self.variant_hint_label.show()
            self.load_button.setVisible(False)
        else:
            self.name_label.setText(entry.display_name)
            self.detail_label.setText(entry.relative_path)
            if self._can_expand and entry.variants:
                arrow = "▼" if self._is_expanded else "▶"
                count = len(entry.variants)
                label = "variant" if count == 1 else "variants"
                self.variant_hint_label.setText(f"{arrow} {count} extra {label}")
                self.variant_hint_label.setToolTip("Click card to expand/collapse variants")
                self.variant_hint_label.show()
                self.load_button.setText("Load Default")
                self.load_button.setVisible(True)
            else:
                self.variant_hint_label.hide()
                self.load_button.setVisible(False)

    def set_thumbnail(self, pixmap: QPixmap):
        """Set the thumbnail image."""
        if pixmap and not pixmap.isNull():
            self.image_label.setPixmap(pixmap)
            self._thumbnail_loaded = True

    def needs_thumbnail(self) -> bool:
        """Check if this card needs its thumbnail loaded."""
        return self.entry is not None and not self._thumbnail_loaded

    def get_image_path(self) -> Optional[str]:
        """Get the image path for this card's entry."""
        return self.entry.image_path if self.entry else None

    def _handle_load_clicked(self):
        if callable(self._callback) and self.entry:
            self._callback(self.entry, None, "select")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if callable(self._callback) and self.entry:
                if self.variant_option is not None:
                    self._callback(self.entry, self.variant_option, "select")
                elif self._can_expand:
                    self._callback(self.entry, None, "toggle")
                else:
                    self._callback(self.entry, None, "select")
        super().mousePressEvent(event)


class MonsterBrowserDialog(QDialog):
    """Dialog that displays monster portraits for quick selection."""

    def __init__(
        self,
        entries: Iterable[MonsterBrowserEntry],
        *,
        initial_columns: int = 3,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Monster Browser")
        self.resize(900, 700)
        
        self._all_entries: List[MonsterBrowserEntry] = list(entries)
        self._filtered_entries: List[MonsterBrowserEntry] = list(self._all_entries)
        self.selected_entry: Optional[MonsterBrowserEntry] = None
        self._columns = max(1, initial_columns)
        self._thumb_size = QSize(140, 140)
        self._expanded_tokens: Set[str] = set()
        self._display_payloads: List[Tuple[MonsterBrowserEntry, Optional[MonsterVariantOption]]] = []
        
        # Card pool for recycling
        self._card_pool: List[MonsterCardWidget] = []
        self._visible_cards: Dict[int, MonsterCardWidget] = {}  # index -> card
        
        # Background thumbnail loader
        self._thumbnail_loader = ThumbnailLoader(self._thumb_size, self)
        self._thumbnail_loader.thumbnail_ready.connect(self._on_thumbnail_ready)
        
        # Debounced search
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._apply_pending_filter)
        self._pending_search: str = ""
        
        # Lazy loading timer
        self._lazy_load_timer = QTimer(self)
        self._lazy_load_timer.setSingleShot(True)
        self._lazy_load_timer.setInterval(50)
        self._lazy_load_timer.timeout.connect(self._load_visible_thumbnails)
        
        # Path to card mapping for thumbnail updates
        self._path_to_cards: Dict[str, List[MonsterCardWidget]] = {}

        self._setup_ui()
        self._apply_filter("")

    def _setup_ui(self):
        """Set up the dialog UI."""
        main_layout = QVBoxLayout(self)
        
        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Search:"))
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search monsters...")
        self.search_input.setClearButtonEnabled(True)
        toolbar.addWidget(self.search_input, 1)

        toolbar.addWidget(QLabel("Columns:"))
        self.columns_spin = QSpinBox()
        self.columns_spin.setRange(1, 8)
        self.columns_spin.setValue(self._columns)
        self.columns_spin.setFixedWidth(50)
        toolbar.addWidget(self.columns_spin)

        self.force_reexport_check = QCheckBox("Re-export JSON")
        self.force_reexport_check.setToolTip("Re-export JSON from BIN before loading")
        toolbar.addWidget(self.force_reexport_check)

        # Option to apply selected animations to the currently active monster
        self.apply_to_active_check = QCheckBox("Apply animations to active monster")
        self.apply_to_active_check.setToolTip("Instead of loading this monster, apply its animations to the currently loaded monster")
        toolbar.addWidget(self.apply_to_active_check)

        main_layout.addLayout(toolbar)

        # Scroll area with grid
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll)
        
        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(10, 10, 10, 10)
        self.grid_layout.setHorizontalSpacing(8)
        self.grid_layout.setVerticalSpacing(8)
        self.scroll_area.setWidget(self.grid_container)
        main_layout.addWidget(self.scroll_area, 1)

        # Status bar
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: gray;")
        main_layout.addWidget(self.status_label)

        # Buttons
        button_row = QHBoxLayout()
        button_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        button_row.addWidget(close_btn)
        main_layout.addLayout(button_row)

        # Connect signals
        self.search_input.textChanged.connect(self._queue_filter_update)
        self.columns_spin.valueChanged.connect(self._update_columns)

    def _queue_filter_update(self, text: str):
        """Queue a filter update with debouncing."""
        self._pending_search = text or ""
        self._search_timer.start()

    def _apply_pending_filter(self):
        """Apply the pending search filter."""
        self._apply_filter(self._pending_search)

    def _apply_filter(self, text: str):
        """Filter entries and rebuild the grid."""
        normalized = text.lower().strip()
        tokens = [token for token in normalized.split() if token]
        
        if tokens:
            self._filtered_entries = [
                entry for entry in self._all_entries 
                if all(token in entry.search_blob for token in tokens)
            ]
        else:
            self._filtered_entries = list(self._all_entries)
        
        self._rebuild_grid()
        self._update_status(normalized)

    def _update_status(self, text: str):
        """Update the status label."""
        total = len(self._all_entries)
        match = len(self._filtered_entries)
        if not total:
            self.status_label.setText("No monsters found in this dataset.")
        elif text and not match:
            self.status_label.setText(f"No monsters match '{text}'.")
        else:
            self.status_label.setText(f"Showing {match} of {total} monsters.")

    def _update_columns(self, value: int):
        """Handle column count change."""
        self._columns = max(1, value)
        self._rebuild_grid()

    def _get_card(self) -> MonsterCardWidget:
        """Get a card from the pool or create a new one."""
        if self._card_pool:
            return self._card_pool.pop()
        return MonsterCardWidget(
            self._thumb_size,
            self._handle_card_clicked,
            parent=self.grid_container,
        )

    def _return_card(self, card: MonsterCardWidget):
        """Return a card to the pool."""
        card.hide()
        card.entry = None
        card.variant_option = None
        self._card_pool.append(card)

    def _clear_grid(self):
        """Clear all cards from the grid."""
        # Return all visible cards to pool
        for card in self._visible_cards.values():
            self._return_card(card)
        self._visible_cards.clear()
        self._path_to_cards.clear()
        
        # Remove any remaining widgets
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget and widget not in self._card_pool:
                widget.hide()

    def _rebuild_grid(self):
        """Rebuild the grid with current filtered entries."""
        self._clear_grid()
        
        if not self._filtered_entries:
            placeholder = QLabel("No monsters available.")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.grid_layout.addWidget(placeholder, 0, 0)
            return

        visible_tokens = {entry.token for entry in self._filtered_entries}
        self._expanded_tokens.intersection_update(visible_tokens)

        self._display_payloads = []
        for entry in self._filtered_entries:
            self._display_payloads.append((entry, None))
            if entry.token in self._expanded_tokens and entry.variants:
                for variant in entry.variants:
                    self._display_payloads.append((entry, variant))

        columns = max(1, self._columns)
        
        # Create cards for all payloads (base entry + expanded variants)
        for idx, payload in enumerate(self._display_payloads):
            entry, variant = payload
            row = idx // columns
            col = idx % columns
            
            card = self._get_card()
            can_expand = variant is None and entry.has_variants()
            expanded = entry.token in self._expanded_tokens
            card.set_entry(entry, variant, can_expand=can_expand, expanded=expanded)
            card.show()
            
            self.grid_layout.addWidget(card, row, col)
            self._visible_cards[idx] = card
            
            # Track path to card mapping
            path = entry.image_path
            if path:
                if path not in self._path_to_cards:
                    self._path_to_cards[path] = []
                self._path_to_cards[path].append(card)

        # Add column stretch
        for col in range(columns):
            self.grid_layout.setColumnStretch(col, 1)
        
        # Schedule lazy loading
        self._lazy_load_timer.start()

    def _on_scroll(self):
        """Handle scroll events to trigger lazy loading."""
        self._lazy_load_timer.start()

    def _load_visible_thumbnails(self):
        """Load thumbnails for currently visible cards."""
        if not self._visible_cards:
            return
        
        viewport = self.scroll_area.viewport()
        if not viewport:
            return
        
        viewport_rect = viewport.rect()
        scroll_pos = self.scroll_area.verticalScrollBar().value()
        
        # Calculate visible area in grid coordinates
        visible_top = scroll_pos
        visible_bottom = scroll_pos + viewport_rect.height()
        
        # Check each card for visibility
        for idx, card in self._visible_cards.items():
            if not card.needs_thumbnail():
                continue
            
            # Get card position relative to scroll area
            card_pos = card.mapTo(self.grid_container, card.rect().topLeft())
            card_top = card_pos.y()
            card_bottom = card_top + card.height()
            
            # Check if card is visible (with some margin for preloading)
            margin = 200  # Preload cards slightly outside viewport
            if card_bottom >= visible_top - margin and card_top <= visible_bottom + margin:
                image_path = card.get_image_path()
                if image_path:
                    # Check cache first
                    cached = self._thumbnail_loader.get_cached(image_path)
                    if cached:
                        card.set_thumbnail(cached)
                    else:
                        self._thumbnail_loader.request_thumbnail(image_path)

    def _on_thumbnail_ready(self, image_path: str, pixmap: QPixmap):
        """Handle thumbnail loaded from background thread."""
        cards = self._path_to_cards.get(image_path, [])
        for card in cards:
            if card.entry and card.entry.image_path == image_path:
                card.set_thumbnail(pixmap)

    def _handle_card_clicked(
        self,
        entry: MonsterBrowserEntry,
        variant: Optional[MonsterVariantOption],
        action: str,
    ):
        """Handle card click or expansion toggle."""
        if action == "toggle" and variant is None and entry.has_variants():
            token = entry.token
            if token in self._expanded_tokens:
                self._expanded_tokens.remove(token)
            else:
                self._expanded_tokens.add(token)
            self._rebuild_grid()
            return
        self._select_entry(entry, variant)

    def _select_entry(
        self,
        entry: MonsterBrowserEntry,
        variant: Optional[MonsterVariantOption],
    ):
        """Finalize selection for either the base entry or a variant."""
        if variant:
            variant_label = variant.variant_label or variant.display_name or entry.display_name
            display_name = f"{entry.display_name} ({variant_label})"
            selection = MonsterBrowserEntry(
                token=entry.token,
                display_name=display_name,
                relative_path=variant.relative_path or entry.relative_path,
                image_path=entry.image_path,
                json_path=variant.json_path,
                bin_path=variant.bin_path,
                variants=[],
            )
        else:
            selection = entry
        self.selected_entry = selection
        self.accept()

    def force_reexport(self) -> bool:
        """Check if re-export is requested."""
        return self.force_reexport_check.isChecked()

    def apply_animations_to_active(self) -> bool:
        """Check if animations should be applied to the active monster instead of loading."""
        return getattr(self, "apply_to_active_check", None) and self.apply_to_active_check.isChecked()

    def column_count(self) -> int:
        """Get current column count."""
        return self._columns

    def closeEvent(self, event):
        """Clean up on close."""
        self._thumbnail_loader.shutdown()
        super().closeEvent(event)
    
    def reject(self):
        """Clean up on reject."""
        self._thumbnail_loader.shutdown()
        super().reject()
    
    def accept(self):
        """Clean up on accept."""
        self._thumbnail_loader.shutdown()
        super().accept()
