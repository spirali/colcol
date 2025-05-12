import sys
import os
import argparse
import unidecode
from dataclasses import dataclass
from datetime import datetime

from dotenv import load_dotenv
import base64
from PIL import Image

load_dotenv()


from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QVBoxLayout,
    QMessageBox,
    QTextEdit,
)
from PySide6.QtGui import (
    QPixmap,
    QPainter,
    QPen,
    QColor,
    QMouseEvent,
    QKeyEvent,
    QKeySequence,
    QShortcut,
)
from PySide6.QtCore import Qt, QPoint, QRect, Signal, Slot, QThread, QSize, QEvent

from PIL import Image, ImageDraw

from openai import OpenAI

INIT_IMAGE_PATH = "../init.png"
DEFAULT_IMAGE_INDEX = 0

TEXT_LIMIT = 350

# If you change this, you need to also change what gpt-image-1 is returning!
IMAGE_W = 1536
IMAGE_H = 1024
RECT_SIZE = 200

client = OpenAI()


@dataclass
class Annotation:
    rect: QRect
    text: str


POS_NAMES = {
    (0, 0): "the top left corner",
    (1, 0): "the top center",
    (2, 0): "the top right corner",
    (0, 1): "the left center",
    (1, 1): "the middle of the image",
    (2, 1): "the right center",
    (0, 2): "the bottom left corner",
    (1, 2): "the bottom center",
    (2, 2): "the bottom right corner",
}


def name_position(x, y):
    rx = min(max(0, x / IMAGE_W), 0.9999)
    ry = min(max(0, y / IMAGE_H), 0.9999)

    ax = int(rx * 3)
    ay = int(ry * 3)

    return POS_NAMES[(ax, ay)]


class ComputationThread(QThread):
    computation_finished = Signal(str)

    def __init__(self, current_image_path, annotation):
        super().__init__()
        self.current_image_path = current_image_path
        self.annotation = annotation
        self._is_running = True

    def run(self):
        print(self.annotation)

        mask = Image.new("RGBA", (IMAGE_W, IMAGE_H))
        draw = ImageDraw.Draw(mask)
        draw.rectangle((0, 0, IMAGE_W, IMAGE_H), fill="#000000ff")
        draw.rectangle(self.annotation.rect.getCoords(), fill="#ffffff00")
        mask.save("tmp-mask.png")
        center = self.annotation.rect.center()
        pos_name = name_position(center.x(), center.y())
        prompt = f"Add the following objects into {pos_name}: {self.annotation.text}. Keep the colors bright and realistic"
        # In case of running on Windows, ... (it has some problems to write the final file)
        prompt = unidecode.unidecode(prompt)
        print(prompt)
        result = client.images.edit(
            model="gpt-image-1",
            image=open(self.current_image_path, "rb"),
            mask=open("tmp-mask.png", "rb"),
            prompt=prompt,
            size="1536x1024",
        )
        image_base64 = result.data[0].b64_json
        image_bytes = base64.b64decode(image_base64)

        now = datetime.now().isoformat()

        # In case of runnig on Windows ...
        now = now.replace(":", "_")
        now = now.replace(".", "_")

        output_filename = f"out-{now}.png"
        output_filename_meta = f"out-{now}.txt"

        with open(output_filename, "wb") as f:
            f.write(image_bytes)

        with open(output_filename_meta, "w") as f:
            f.write(prompt)
        self.computation_finished.emit(output_filename)

    def stop(self):
        self._is_running = False


class LimitedTextEdit(QTextEdit):
    def __init__(self, max_length=10, parent=None):
        super().__init__(parent)
        self.max_length = max_length
        self.textChanged.connect(self.limit_text)

    def limit_text(self):
        text = self.toPlainText()
        if len(text) > self.max_length:
            cursor = self.textCursor()
            position = cursor.position()

            new_text = text[: self.max_length]

            self.setPlainText(new_text)

            if position <= self.max_length:
                cursor.setPosition(position)
                self.setTextCursor(cursor)
            else:
                cursor.setPosition(self.max_length)
                self.setTextCursor(cursor)


class InteractiveImageWidget(QWidget):
    """Widget for image display and single annotation interaction."""

    # Signal: Emitted when Ctrl+Enter is pressed in the text input
    # Arguments: rectangle (QRect), text (str)
    annotation_finalized = Signal(Annotation)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        # Store only one annotation, or None
        self.current_annotation = None
        # Relative rectangle size (percentage of image dimensions)
        self.TEXT_INPUT_OFFSET_Y = 5  # Small fixed offset for text input

        # Replace QLineEdit with QTextEdit
        self.text_input = LimitedTextEdit(TEXT_LIMIT, self)
        self.text_input.setHidden(True)
        self.text_input.setStyleSheet(
            "background-color: rgba(0, 0, 0, 0.5); color: white; maximumLength: 16"
        )
        font = self.text_input.font()
        font.setPointSize(14)
        self.text_input.setFont(font)
        self.text_input.setFixedWidth(300)
        self.text_input.setFixedHeight(80)  # Height for approximately 3 lines
        self.text_input.setTabChangesFocus(
            True
        )  # Tab moves focus instead of inserting tab

        # We'll handle keyPressEvent for the QTextEdit to catch Ctrl+Enter
        self.text_input.installEventFilter(self)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._is_enabled = True  # Track enabled state for visual feedback

    def set_pixmap(self, pixmap):
        self._pixmap = pixmap
        self.clear_current_annotation()
        self.updateGeometry()
        self.update()

    def setEnabled(self, enabled):
        """Override setEnabled to control interaction and visuals."""
        self._is_enabled = enabled
        super().setEnabled(enabled)  # Let Qt handle disabling children (like QTextEdit)
        if not enabled:
            self.text_input.setHidden(True)  # Hide input when disabled
        self.update()  # Repaint to show potential visual changes (like opacity)

    def get_image_display_rect(self):
        if self._pixmap.isNull():
            return QRect()
        widget_rect = self.rect()
        pixmap_size = self._pixmap.size()
        scaled_size = pixmap_size.scaled(
            widget_rect.size(), Qt.AspectRatioMode.KeepAspectRatio
        )
        display_rect = QRect(QPoint(0, 0), scaled_size)
        display_rect.moveCenter(widget_rect.center())
        return display_rect

    def clear_current_annotation(self):
        """Removes the current annotation and hides the text input."""
        if self.current_annotation:
            self.current_annotation = None
            self.text_input.setHidden(True)
            self.update()

    def create_or_update_annotation(self, pos):
        """Creates a new annotation or moves the existing one."""
        img_rect = self.get_image_display_rect()
        if not img_rect.isValid():
            return

        x = pos.x() - img_rect.left()
        y = pos.y() - img_rect.top()
        x = int(x * 1538 / img_rect.width())
        y = int(y * 1024 / img_rect.height())
        x = min(x, 1538 - RECT_SIZE)
        y = min(y, 1024 - RECT_SIZE)
        new_rect = QRect(x, y, RECT_SIZE, RECT_SIZE)

        current_text = ""
        if self.current_annotation:
            current_text = self.current_annotation.text  # Keep text if moving

        self.current_annotation = Annotation(rect=new_rect, text=current_text)
        self.text_input.setText(current_text)
        self.reposition_text_input(img_rect)
        self.text_input.setHidden(False)
        self.text_input.setFocus()  # Focus input immediately
        self.update()

    def mousePressEvent(self, event: QMouseEvent):
        if not self._is_enabled:
            return  # Ignore clicks when disabled

        if event.button() == Qt.MouseButton.LeftButton:
            clicked_pos = event.pos()
            img_rect = self.get_image_display_rect()

            if img_rect.contains(clicked_pos):
                # Always create/update the single annotation
                self.create_or_update_annotation(clicked_pos)
            else:
                # Click outside image: clear annotation and focus
                self.clear_current_annotation()
                self.setFocus()  # Give focus back to widget

        super().mousePressEvent(event)

    def eventFilter(self, obj, event):
        """Filter events to capture Ctrl+Enter in QTextEdit"""
        if obj is self.text_input and event.type() == QEvent.Type.KeyPress:
            key_event = QKeyEvent(event)
            if (
                key_event.key() == Qt.Key.Key_Return
                or key_event.key() == Qt.Key.Key_Enter
            ) and key_event.modifiers() == Qt.KeyboardModifier.ControlModifier:
                self.handle_ctrl_enter_pressed()
                return True  # Event handled

        return super().eventFilter(obj, event)

    def handle_ctrl_enter_pressed(self):
        """Called when Ctrl+Enter is pressed in the QTextEdit."""
        if self.current_annotation and self._is_enabled:
            self.current_annotation.text = self.text_input.toPlainText()
            if len(self.current_annotation.text) > 3:
                self.annotation_finalized.emit(self.current_annotation)

    def reposition_text_input(self, img_rect):
        if self.current_annotation:
            rect = self.anotation_screen_rect(img_rect)
            input_x = rect.left()
            input_y = rect.bottom() + self.TEXT_INPUT_OFFSET_Y
            input_x = max(0, min(input_x, self.width() - self.text_input.width()))
            input_y = max(0, min(input_y, self.height() - self.text_input.height()))
            self.text_input.move(input_x, input_y)

    def anotation_screen_rect(self, img_rect):
        if self.current_annotation:
            arect = self.current_annotation.rect
            x = int(img_rect.left() + arect.left() / 1538 * img_rect.width())
            y = int(img_rect.top() + arect.top() / 1024 * img_rect.height())
            w = int(arect.width() / 1538 * img_rect.width())
            h = int(arect.height() / 1024 * img_rect.height())
            return QRect(x, y, w, h)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._pixmap.isNull():
            painter.end()
            return

        img_rect = self.get_image_display_rect()
        opacity = 1.0 if self._is_enabled else 0.5  # Dim when disabled
        painter.setOpacity(opacity)
        painter.drawPixmap(img_rect, self._pixmap)
        painter.setOpacity(1.0)  # Reset opacity

        SELECTED_BORDER_COLOR = QColor(
            255, 0, 0
        )  # Yellow border for selected rectangle
        SELECTED_BORDER_WIDTH = 4

        if self.current_annotation:
            rect = self.anotation_screen_rect(img_rect)
            pen = QPen(SELECTED_BORDER_COLOR)
            pen.setWidth(SELECTED_BORDER_WIDTH)
            painter.setPen(pen)
            painter.drawRect(rect)

            hint_rect = QRect(
                self.text_input.x() + self.text_input.width() - 150,
                self.text_input.y() - 25,
                150,
                25,
            )
            painter.fillRect(hint_rect, QColor(0, 0, 0, 170))

            pen = QPen("white")
            painter.setPen(pen)
            ln = len(self.text_input.toPlainText())
            text = f"{ln}/{TEXT_LIMIT}"
            painter.drawText(
                self.text_input.x() + self.text_input.width() - 50,
                self.text_input.y() + self.text_input.height(),
                text,
            )

            pen = QPen("#999")
            painter.setPen(pen)
            text = f"ctrl+enter to submit"
            painter.drawText(
                self.text_input.x() + self.text_input.width() - 145,
                self.text_input.y() - 5,
                text,
            )

        painter.end()


# --- Main Windows ---


class ProjectorWindow(QWidget):
    def __init__(self, pixmap):
        super().__init__()
        self.setWindowTitle("Projector View")
        self.update_image(pixmap)  # Initial image

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.pixmap.isNull():
            painter.end()
            return

        img_rect = self.get_image_display_rect()
        painter.fillRect(self.rect(), QColor(0, 0, 0))
        painter.drawPixmap(img_rect, self.pixmap)

    def get_image_display_rect(self):
        if self.pixmap.isNull():
            return QRect()
        widget_rect = self.rect()
        pixmap_size = self.pixmap.size()
        scaled_size = pixmap_size.scaled(
            widget_rect.size(), Qt.AspectRatioMode.KeepAspectRatio
        )
        display_rect = QRect(QPoint(0, 0), scaled_size)
        display_rect.moveCenter(widget_rect.center())
        return display_rect

    @Slot(QPixmap)
    def update_image(self, pixmap):
        self.pixmap = pixmap
        self.update()


class ControlWindow(QWidget):
    request_projector_update = Signal(QPixmap)

    def __init__(self, initial_pixmap, initial_image_path):
        super().__init__()
        self.setWindowTitle("Control Panel")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.current_image_path = initial_image_path
        self.computation_thread = None

        self.image_widget = InteractiveImageWidget()
        self.image_widget.set_pixmap(initial_pixmap)
        self.image_widget.annotation_finalized.connect(self.start_computation)

        # Overlay for "Working in Progress"
        self.working_overlay = QLabel(
            "Updating the image\n(taskes about 1 minute)", self
        )
        self.working_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.working_overlay.setStyleSheet("""
            background-color: rgba(0, 0, 0, 0.7);
            color: white;
            font-size: 24px;
            font-weight: bold;
            border-radius: 10px;
        """)
        self.working_overlay.setHidden(True)  # Initially hidden
        # Ensure overlay covers the image widget
        self.working_overlay.lower()  # Put it below text input initially
        self.image_widget.text_input.stackUnder(
            self.working_overlay
        )  # Ensure overlay is above text input

        self.layout.addWidget(self.image_widget)
        self.setLayout(self.layout)
        self.image_widget.setFocus()

        # Global Close Shortcut (Ctrl+F11)
        self.close_shortcut = QShortcut(QKeySequence("Ctrl+F11"), self)
        self.close_shortcut.activated.connect(self.close_application)

    def resizeEvent(self, event):
        """Ensure overlay covers the image widget area on resize."""
        super().resizeEvent(event)
        # Simple approach: cover the whole window. Adjust if needed.
        self.working_overlay.setGeometry(self.rect())

    @Slot(QRect, str)
    def start_computation(self, annotation):
        print(f"Annotation finalized: Rect={annotation.rect}, Text='{annotation.text}'")
        print("Starting simulated computation...")

        # Show overlay and disable interaction
        self.working_overlay.raise_()  # Bring overlay to top
        self.working_overlay.setHidden(False)
        self.image_widget.setEnabled(False)

        # Start computation thread
        if self.computation_thread and self.computation_thread.isRunning():
            self.computation_thread.stop()  # Stop previous if any
            self.computation_thread.wait()  # Wait for it to finish stopping

        self.computation_thread = ComputationThread(self.current_image_path, annotation)
        self.computation_thread.computation_finished.connect(self.computation_finished)
        self.computation_thread.start()

    @Slot(str)
    def computation_finished(self, next_image_path):
        print(f"Computation thread finished. Loading: {next_image_path}")
        self.current_image_path = next_image_path

        new_pixmap = QPixmap(self.current_image_path)
        if new_pixmap.isNull():
            print(f"Error: Failed to load image {self.current_image_path}")
            QMessageBox.warning(
                self, "Image Error", f"Failed to load image:\n{self.current_image_path}"
            )
        else:
            self.image_widget.set_pixmap(new_pixmap)
            self.request_projector_update.emit(new_pixmap)

        self.working_overlay.setHidden(True)
        self.image_widget.setEnabled(True)
        self.image_widget.setFocus()  # Give focus back

    @Slot()
    def close_application(self):
        print("Ctrl+F11 detected. Closing application.")
        if self.computation_thread and self.computation_thread.isRunning():
            self.computation_thread.stop()
            self.computation_thread.wait(2000)
        QApplication.instance().quit()

    def closeEvent(self, event):
        print("Control window close event.")
        self.close_application()  # Ensure cleanup and closes projector
        super().closeEvent(event)


# --- Main Execution ---


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    parser = argparse.ArgumentParser(description="Colaborative Collage")
    parser.add_argument(
        "--fullscreen", action="store_true", help="Run in fullscreen mode."
    )
    args = parser.parse_args()

    app = QApplication(sys.argv)

    current_image_path = INIT_IMAGE_PATH
    if not os.path.exists(current_image_path):
        QMessageBox.critical(
            None,
            "File Error",
            f"Initial image file not found:\n{os.path.abspath(current_image_path)}",
        )
        sys.exit(1)
    initial_pixmap = QPixmap(current_image_path)
    if initial_pixmap.isNull():
        QMessageBox.critical(
            None,
            "Image Error",
            f"Failed to load initial image:\n{os.path.abspath(current_image_path)}",
        )
        sys.exit(1)

    # --- Screen Detection ---
    screens = app.screens()
    primary_screen = app.primaryScreen()
    secondary_screen = None

    if len(screens) < 2:
        print(
            "Warning: Only one screen detected. Both windows will show on the primary screen."
        )
        secondary_screen = primary_screen
    else:
        # Find secondary (adjust logic if needed based on primary screen name/geometry)
        for screen in screens:
            if screen != primary_screen:
                secondary_screen = screen
                break
        secondary_screen = secondary_screen or primary_screen  # Fallback
        print(f"Primary screen: {primary_screen.name()} ({primary_screen.geometry()})")
        print(
            f"Secondary screen: {secondary_screen.name()} ({secondary_screen.geometry()})"
        )

    # --- Create Windows ---
    control_window = ControlWindow(initial_pixmap, current_image_path)
    projector_window = ProjectorWindow(initial_pixmap)

    control_window.request_projector_update.connect(projector_window.update_image)

    # --- Assign Screens and Show ---
    control_window.setScreen(primary_screen)
    projector_window.setScreen(secondary_screen)

    if args.fullscreen:
        print("Starting in fullscreen mode.")
        screen = projector_window.screen()
        projector_window.move(screen.geometry().topLeft())
        control_window.showFullScreen()
        projector_window.showFullScreen()
    else:
        print("Starting in windowed mode.")
        control_window.resize(QSize(800, 600))
        projector_window.resize(QSize(800, 600))
        control_window.show()
        projector_window.show()

    control_window.activateWindow()
    control_window.raise_()
    control_window.image_widget.setFocus()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
