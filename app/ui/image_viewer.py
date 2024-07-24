# Import third-party modules
from PySide6 import QtWidgets
from PySide6 import QtCore, QtGui

import cv2
import numpy as np
import math
from typing import List, Dict

class ImageViewer(QtWidgets.QGraphicsView):
    rectangle_selected = QtCore.Signal(QtCore.QRectF)
    rectangle_changed = QtCore.Signal(QtCore.QRectF)

    def __init__(self, parent):
        super().__init__(parent)
        self._zoom = 0
        self._empty = True
        self._scene = QtWidgets.QGraphicsScene(self)
        self._photo = QtWidgets.QGraphicsPixmapItem()
        self._photo.setShapeMode(QtWidgets.QGraphicsPixmapItem.BoundingRectShape)
        self._scene.addItem(self._photo)
        self.setScene(self._scene)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setBackgroundBrush(QtGui.QBrush(QtGui.QColor(30, 30, 30)))
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._current_tool = None
        self._box_mode = False
        self._dragging = False
        self._start_point = None
        self._current_rect = None
        self._rectangles = []
        self._selected_rect = None
        self._drag_start = None
        self._drag_offset = None
        self._resize_handle = None
        self._resize_start = None
        self._panning = False
        self._pan_start_pos = None

        self._brush_color = QtGui.QColor(255, 0, 0, 100)
        self._brush_size = 25
        self._drawing_path = None
        self._drawing_items = []
        self._undo_brush_stack = []
        self._redo_brush_stack = []
        self._eraser_size = 25

        self._brush_cursor = self.create_inpaint_cursor('brush', self._brush_size)
        self._eraser_cursor = self.create_inpaint_cursor('eraser', self._eraser_size)

        self._current_path = None
        self._current_path_item = None

    def hasPhoto(self):
        return not self._empty

    def wheelEvent(self, event):
        if self.hasPhoto():
            factor = 1.25
            if event.angleDelta().y() > 0:
                self.scale(factor, factor)
                self._zoom += 1
            else:
                self.scale(1 / factor, 1 / factor)
                self._zoom -= 1

    def fitInView(self):
        rect = QtCore.QRectF(self._photo.pixmap().rect())
        if not rect.isNull():
            self.setSceneRect(rect)
            if self.hasPhoto():
                unity = self.transform().mapRect(QtCore.QRectF(0, 0, 1, 1))
                self.scale(1 / unity.width(), 1 / unity.height())
                viewrect = self.viewport().rect()
                scenerect = self.transform().mapRect(rect)
                factor = min(viewrect.width() / scenerect.width(),
                             viewrect.height() / scenerect.height())
                self.scale(factor, factor)
                self.centerOn(rect.center())

    def set_tool(self, tool: str):
        self._current_tool = tool
        
        if tool == 'pan':
            self.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag)
        elif tool in ['brush', 'eraser']:
            self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
            #self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
            if tool == 'brush':
                self.setCursor(self._brush_cursor)
            else:
                self.setCursor(self._eraser_cursor)
        else:
            self.setDragMode(QtWidgets.QGraphicsView.NoDrag)

    def delete_selected_rectangle(self):
        if self._selected_rect:
            self._scene.removeItem(self._selected_rect)
            self._rectangles.remove(self._selected_rect)
            self._selected_rect = None

    def mousePressEvent(self, event):

        if event.button() == QtCore.Qt.MiddleButton:
            self._panning = True
            self._pan_start_pos = event.position()
            self.viewport().setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        
        if self._current_tool in ['brush', 'eraser'] and self.hasPhoto():
            self._drawing_path = QtGui.QPainterPath()
            scene_pos = self.mapToScene(event.position().toPoint())
            if self._photo.contains(scene_pos):
                self._drawing_path.moveTo(scene_pos)
                self._current_path = QtGui.QPainterPath()
                self._current_path.moveTo(scene_pos)
                self._current_path_item = self._scene.addPath(self._current_path, 
                                                              QtGui.QPen(self._brush_color, self._brush_size, 
                                                                         QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, 
                                                                         QtCore.Qt.RoundJoin))

        if self._current_tool == 'box' and self.hasPhoto():
            scene_pos = self.mapToScene(event.position().toPoint())
            if self._photo.contains(scene_pos):
                item = self.itemAt(event.position().toPoint())
                if isinstance(item, QtWidgets.QGraphicsRectItem) and item != self._photo:
                    self.select_rectangle(item)
                    handle = self.get_resize_handle(item, scene_pos)
                    if handle:
                        self._resize_handle = handle
                        self._resize_start = scene_pos
                    else:
                        self._dragging = True
                        self._drag_start = scene_pos
                        self._drag_offset = scene_pos - item.rect().topLeft()
                else:
                    self.deselect_all()
                    self._box_mode = True
                    self._start_point = scene_pos
                    self._current_rect = QtWidgets.QGraphicsRectItem(self._photo.mapRectToItem(self._photo, QtCore.QRectF(self._start_point, self._start_point)))
                    self._current_rect.setBrush(QtGui.QBrush(QtGui.QColor(255, 192, 203, 100)))  # Transparent pink
                    self._scene.addItem(self._current_rect)

        elif self._current_tool == 'pan':
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
    
        if self._panning:
            new_pos = event.position()
            delta = new_pos - self._pan_start_pos
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self._pan_start_pos = new_pos
            event.accept()
            return
        
        if self._current_tool in ['brush', 'eraser'] and self._current_path:
            scene_pos = self.mapToScene(event.position().toPoint())
            if self._photo.contains(scene_pos):
                self._current_path.lineTo(scene_pos)
                if self._current_tool == 'brush':
                    self._current_path_item.setPath(self._current_path)
                elif self._current_tool == 'eraser':
                    self.erase_at(scene_pos)

        if self._current_tool == 'box':
            scene_pos = self.mapToScene(event.position().toPoint())
            if self._box_mode:
                end_point = self.constrain_point(scene_pos)
                rect = QtCore.QRectF(self._start_point, end_point).normalized()
                self._current_rect.setRect(self._photo.mapRectToItem(self._photo, rect))
            elif self._selected_rect:
                if self._resize_handle:
                    self.resize_rectangle(scene_pos)
                elif self._dragging:
                    self.move_rectangle(scene_pos)
                else:
                    cursor = self.get_cursor(self._selected_rect, scene_pos)
                    self.viewport().setCursor(cursor)
            else:
                cursor = self.get_cursor_for_box_tool(scene_pos)
                self.viewport().setCursor(cursor)

    def move_rectangle(self, scene_pos: QtCore.QPointF):
        new_pos = scene_pos
        new_top_left = new_pos - self._drag_offset
        new_rect = QtCore.QRectF(new_top_left, self._selected_rect.rect().size())
        constrained_rect = self.constrain_rect(new_rect)
        self._selected_rect.setRect(constrained_rect)
        
        # Emit the rectangle_moved signal
        self.rectangle_changed.emit(constrained_rect)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.MiddleButton:
            self._panning = False
            self.viewport().setCursor(QtCore.Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        
        if self._current_tool in ['brush', 'eraser']:
            if self._current_path_item:
                self._drawing_items.append(self._current_path_item)
            self._current_path = None
            self._current_path_item = None
            self._undo_brush_stack.append(self._drawing_items)
            self._drawing_items = []
            self._redo_brush_stack.clear()

        if self._current_tool == 'box':
            if self._box_mode:
                self._box_mode = False
                if self._current_rect and self._current_rect.rect().width() > 0 and self._current_rect.rect().height() > 0:
                    self._rectangles.append(self._current_rect)
                else:
                    self._scene.removeItem(self._current_rect)
                self._current_rect = None
            else:
                self._dragging = False
                self._drag_offset = None
                self._resize_handle = None
                self._resize_start = None

        elif self._current_tool == 'pan':
            super().mouseReleaseEvent(event)
            
    def erase_at(self, pos: QtCore.QPointF):
        erase_path = QtGui.QPainterPath()
        erase_path.addEllipse(pos, self._eraser_size, self._eraser_size)
        
        # Increase precision of eraser path
        precise_erase_path = QtGui.QPainterPath()
        for i in range(36):
            angle = i * 10 * 3.14159 / 180
            point = QtCore.QPointF(pos.x() + self._eraser_size * math.cos(angle),
                                pos.y() + self._eraser_size * math.sin(angle))
            if i == 0:
                precise_erase_path.moveTo(point)
            else:
                precise_erase_path.lineTo(point)
        precise_erase_path.closeSubpath()

        items = self._scene.items(erase_path)
        for item in items:
            if isinstance(item, QtWidgets.QGraphicsPathItem) and item != self._photo:
                path = item.path()
                intersected_path = path.intersected(precise_erase_path)
                if not intersected_path.isEmpty():
                    new_path = QtGui.QPainterPath(path)
                    new_path = new_path.subtracted(intersected_path)
                    item.setPath(new_path)
                    if new_path.isEmpty():
                        self._scene.removeItem(item)
                        if item in self._drawing_items:
                            self._drawing_items.remove(item)

    def save_brush_strokes(self):
        brush_strokes = []
        for item in self._scene.items():
            if isinstance(item, QtWidgets.QGraphicsPathItem):
                brush_strokes.append({
                    'path': item.path(),
                    'pen': item.pen().color().name(QtGui.QColor.HexArgb),  # Save with alpha
                    'brush': item.brush().color().name(QtGui.QColor.HexArgb),  # Save fill color
                    'width': item.pen().width()
                })
        return brush_strokes

    def load_brush_strokes(self, brush_strokes: List[Dict]):
        self.clear_brush_strokes()
        # Reverse the brush_strokes list
        reversed_brush_strokes = brush_strokes[::-1]
        for stroke in reversed_brush_strokes:
            pen = QtGui.QPen()
            pen.setColor(QtGui.QColor(stroke['pen']))
            pen.setWidth(stroke['width'])
            pen.setStyle(QtCore.Qt.SolidLine)
            pen.setCapStyle(QtCore.Qt.RoundCap)
            pen.setJoinStyle(QtCore.Qt.RoundJoin)

            brush = QtGui.QBrush(QtGui.QColor(stroke['brush']))
            brush_color = QtGui.QColor(stroke['brush'])
            if brush_color == "#80ff0000":
                path_item = self._scene.addPath(stroke['path'], pen, brush)
            else:
                path_item = self._scene.addPath(stroke['path'], pen)
            self._drawing_items.append(path_item)
            self._undo_brush_stack.append(self._drawing_items)
            self._drawing_items = []
            #self._redo_brush_stack.clear()

    def undo_brush_stroke(self):
        if self._undo_brush_stack:
            items = self._undo_brush_stack.pop()
            for item in items:
                self._scene.removeItem(item)
            self._redo_brush_stack.append(items)

    def redo_brush_stroke(self):
        if self._redo_brush_stack:
            items = self._redo_brush_stack.pop()
            for item in items:
                self._scene.addItem(item)
            self._undo_brush_stack.append(items)

    def clear_brush_strokes(self):
        items_to_remove = []
        for item in self._scene.items():
            if isinstance(item, QtWidgets.QGraphicsPathItem) and item != self._photo:
                items_to_remove.append(item)
        
        for item in items_to_remove:
            self._scene.removeItem(item)
        
        self._drawing_items.clear()
        self._undo_brush_stack.clear()
        self._redo_brush_stack.clear()
        
        # Update the scene to reflect the changes
        self._scene.update()

    def get_resize_handle(self, rect: QtWidgets.QGraphicsRectItem, pos: QtCore.QPointF):
        handle_size = 20
        rect_rect = rect.rect()
        top_left = rect_rect.topLeft()
        bottom_right = rect_rect.bottomRight()
        
        handles = {
            'top_left': QtCore.QRectF(top_left.x() - handle_size/2, top_left.y() - handle_size/2, handle_size, handle_size),
            'top_right': QtCore.QRectF(bottom_right.x() - handle_size/2, top_left.y() - handle_size/2, handle_size, handle_size),
            'bottom_left': QtCore.QRectF(top_left.x() - handle_size/2, bottom_right.y() - handle_size/2, handle_size, handle_size),
            'bottom_right': QtCore.QRectF(bottom_right.x() - handle_size/2, bottom_right.y() - handle_size/2, handle_size, handle_size),
            'top': QtCore.QRectF(top_left.x(), top_left.y() - handle_size/2, rect_rect.width(), handle_size),
            'bottom': QtCore.QRectF(top_left.x(), bottom_right.y() - handle_size/2, rect_rect.width(), handle_size),
            'left': QtCore.QRectF(top_left.x() - handle_size/2, top_left.y(), handle_size, rect_rect.height()),
            'right': QtCore.QRectF(bottom_right.x() - handle_size/2, top_left.y(), handle_size, rect_rect.height()),
        }
        
        for handle, handle_rect in handles.items():
            if handle_rect.contains(pos):
                return handle
        return None

    def get_cursor(self, rect: QtWidgets.QGraphicsRectItem, pos: QtCore.QPointF):
        handle = self.get_resize_handle(rect, pos)
        if handle:
            cursors = {
                'top_left': QtCore.Qt.CursorShape.SizeFDiagCursor,
                'top_right': QtCore.Qt.CursorShape.SizeBDiagCursor,
                'bottom_left': QtCore.Qt.CursorShape.SizeBDiagCursor,
                'bottom_right': QtCore.Qt.CursorShape.SizeFDiagCursor,
                'top': QtCore.Qt.CursorShape.SizeVerCursor,
                'bottom': QtCore.Qt.CursorShape.SizeVerCursor,
                'left': QtCore.Qt.CursorShape.SizeHorCursor,
                'right': QtCore.Qt.CursorShape.SizeHorCursor,
            }
            return cursors.get(handle, QtCore.Qt.CursorShape.ArrowCursor)
        elif rect.rect().contains(pos):
            return QtCore.Qt.CursorShape.SizeAllCursor  # Move cursor when inside the box
        return QtCore.Qt.CursorShape.ArrowCursor
    
    def get_cursor_for_box_tool(self, pos: QtCore.QPointF):
        if self._photo.contains(pos):
            for rect in self._rectangles:
                if rect.rect().contains(pos):
                    return QtCore.Qt.CursorShape.PointingHandCursor  # Click cursor when hovering over a box
            return QtCore.Qt.CursorShape.CrossCursor  # Crosshair cursor when inside the image but not over a box
        return QtCore.Qt.CursorShape.ArrowCursor  # Default arrow cursor when outside the image
    
    def resize_rectangle(self, pos: QtCore.QPointF):
        if not self._selected_rect or not self._resize_handle:
            return

        rect = self._selected_rect.rect()
        dx = pos.x() - self._resize_start.x()
        dy = pos.y() - self._resize_start.y()

        new_rect = QtCore.QRectF(rect)

        if self._resize_handle in ['top_left', 'left', 'bottom_left']:
            new_rect.setLeft(rect.left() + dx)
        if self._resize_handle in ['top_left', 'top', 'top_right']:
            new_rect.setTop(rect.top() + dy)
        if self._resize_handle in ['top_right', 'right', 'bottom_right']:
            new_rect.setRight(rect.right() + dx)
        if self._resize_handle in ['bottom_left', 'bottom', 'bottom_right']:
            new_rect.setBottom(rect.bottom() + dy)

        # Ensure the rectangle doesn't flip inside out
        if new_rect.width() < 10:
            if 'left' in self._resize_handle:
                new_rect.setLeft(new_rect.right() - 10)
            else:
                new_rect.setRight(new_rect.left() + 10)
        if new_rect.height() < 10:
            if 'top' in self._resize_handle:
                new_rect.setTop(new_rect.bottom() - 10)
            else:
                new_rect.setBottom(new_rect.top() + 10)

        constrained_rect = self.constrain_rect(new_rect)
        self._selected_rect.setRect(constrained_rect)
        self._resize_start = pos

        # Emit the rectangle_resized signal
        self.rectangle_changed.emit(constrained_rect)

    def constrain_rect(self, rect: QtCore.QRectF):
        photo_rect = self._photo.boundingRect()
        new_x = max(0, min(rect.x(), photo_rect.width() - rect.width()))
        new_y = max(0, min(rect.y(), photo_rect.height() - rect.height()))
        return QtCore.QRectF(new_x, new_y, rect.width(), rect.height())

    def constrain_point(self, point: QtCore.QPointF):
        return QtCore.QPointF(
            max(0, min(point.x(), self._photo.pixmap().width())),
            max(0, min(point.y(), self._photo.pixmap().height()))
        )

    def select_rectangle(self, rect: QtWidgets.QGraphicsRectItem):
        self.deselect_all()
        rect.setBrush(QtGui.QBrush(QtGui.QColor(255, 0, 0, 100)))  # Transparent red
        self._selected_rect = rect

        self.rectangle_selected.emit(rect.rect())

    def deselect_all(self):
        for rect in self._rectangles:
            rect.setBrush(QtGui.QBrush(QtGui.QColor(255, 192, 203, 100)))  # Transparent pink
        self._selected_rect = None

    def get_rectangle_properties(self):
        return [
            {
                'x': rect.rect().x(),
                'y': rect.rect().y(),
                'width': rect.rect().width(),
                'height': rect.rect().height(),
                'selected': rect == self._selected_rect
            }
            for rect in self._rectangles
        ]

    def get_rectangle_coordinates(self):
        return [
            (
                int(rect.rect().x()),
                int(rect.rect().y()),
                int(rect.rect().x() + rect.rect().width()),
                int(rect.rect().y() + rect.rect().height())
            )
            for rect in self._rectangles
        ]
    
    def get_cv2_image(self):
        """Get the currently loaded image as a cv2 image."""

        if self._photo.pixmap() is None:
            return None

        qimage = self._photo.pixmap().toImage()
        qimage = qimage.convertToFormat(QtGui.QImage.Format.Format_RGB888)

        width = qimage.width()
        height = qimage.height()
        bytes_per_line = qimage.bytesPerLine()

        byte_count = qimage.sizeInBytes()
        expected_size = height * bytes_per_line  # bytes per line can include padding


        if byte_count != expected_size:
            print(f"QImage sizeInBytes: {byte_count}, Expected size: {expected_size}")
            print(f"Image dimensions: ({width}, {height}), Format: {qimage.format()}")
            raise ValueError(f"Byte count mismatch: got {byte_count} but expected {expected_size}")

        ptr = qimage.bits()

        # Convert memoryview to a numpy array considering the complete data with padding
        arr = np.array(ptr).reshape((height, bytes_per_line))

        # Exclude the padding bytes, keeping only the relevant image data
        arr = arr[:, :width * 3]

        # Reshape to the correct dimensions without the padding bytes
        arr = arr.reshape((height, width, 3))

        return cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
    
    def display_cv2_image(self, cv2_img: np.ndarray):
        height, width, channel = cv2_img.shape
        bytes_per_line = 3 * width
        qimage = QtGui.QImage(cv2_img.data, width, height, bytes_per_line, QtGui.QImage.Format.Format_RGB888)
        pixmap = QtGui.QPixmap.fromImage(qimage)
        self.setPhoto(pixmap)

    def clear_scene(self):
        self._scene.clear()
        self._photo = QtWidgets.QGraphicsPixmapItem()
        self._photo.setShapeMode(QtWidgets.QGraphicsPixmapItem.BoundingRectShape)
        self._scene.addItem(self._photo)
        self._rectangles = []
        self._selected_rect = None

    def clear_rectangles(self):
        for rect in self._rectangles:
            self._scene.removeItem(rect)
        self._rectangles.clear()
        self._selected_rect = None

    def setPhoto(self, pixmap: QtGui.QPixmap =None):
        self.clear_scene()
        if pixmap and not pixmap.isNull():
            self._empty = False
            self._photo.setPixmap(pixmap)
            self.fitInView()
        else:
            self._empty = True
        self._zoom = 0

    def has_drawn_elements(self):
        """
        Check if any elements (paths) have been drawn on the image.
        
        Returns:
        bool: True if there are drawn elements, False otherwise.
        """
        for item in self._scene.items():
            if isinstance(item, QtWidgets.QGraphicsPathItem):
                if item != self._photo:
                    return True
        return False

    def generate_mask_from_strokes(self):
        if not self.hasPhoto():
            return None

        # Get the size of the image
        image_rect = self._photo.boundingRect()
        width = int(image_rect.width())
        height = int(image_rect.height())

        # Create a blank mask
        mask = np.zeros((height, width), dtype=np.uint8)

        # Create a QImage to draw all paths
        qimage = QtGui.QImage(width, height, QtGui.QImage.Format_Grayscale8)
        qimage.fill(QtGui.QColor(0, 0, 0))  # Fill with black

        # Create a QPainter to draw on the QImage
        painter = QtGui.QPainter(qimage)
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), self._brush_size))
        painter.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255)))  # Set brush for filling 

        # Iterate through all items in the scene
        for item in self._scene.items():
            # Check if the item is a drawn stroke (path in this case)
            if isinstance(item, QtWidgets.QGraphicsPathItem) and item != self._photo:
                # Draw the path onto the QImage
                painter.drawPath(item.path())

        # End painting
        painter.end()

        # Convert the QImage to a numpy array, accounting for padding
        bytes_per_line = qimage.bytesPerLine()
        ptr = qimage.constBits()
        arr = np.array(ptr).reshape(height, bytes_per_line)
        
        # Exclude the padding bytes, keeping only the relevant image data
        mask = arr[:, :width]

        # Apply dilation to thicken the strokes
        kernel = np.ones((5,5), np.uint8)
        mask = cv2.dilate(mask, kernel, iterations=2)

        return mask

    def get_mask_for_inpainting(self):
        # Generate the mask
        mask = self.generate_mask_from_strokes()

        if mask is None:
            return None

        # Get the current image
        cv2_image = self.get_cv2_image()

        if cv2_image is None:
            return None

        # Ensure the mask and image have the same dimensions
        mask = cv2.resize(mask, (cv2_image.shape[1], cv2_image.shape[0]))

        return mask

    def draw_segmentation_lines(self, segmentation_points: np.ndarray, layers: int = 1):
        if not self.hasPhoto():
            print("No photo loaded.")
            return

        if len(segmentation_points) < 3:
            print("Not enough points to create a filled area.")
            return

        # Create a QPainterPath from the segmentation points
        path = QtGui.QPainterPath()
        path.moveTo(segmentation_points[0][0], segmentation_points[0][1])
        for point in segmentation_points[1:]:
            path.lineTo(point[0], point[1])
        path.closeSubpath()

        # Create the path item and add it to the scene
        fill_color = QtGui.QColor(255, 0, 0, 128)  # Semi-transparent red
        outline_color = QtGui.QColor(255, 0, 0)  # Solid red for the outline
        
        for _ in range(layers):
            path_item = QtWidgets.QGraphicsPathItem(path)
            path_item.setPen(QtGui.QPen(outline_color, 2, QtCore.Qt.SolidLine))
            path_item.setBrush(QtGui.QBrush(fill_color))
            self._scene.addItem(path_item)
            self._drawing_items.append(path_item)  # Add to drawing items for saving

        # Ensure the filled area is visible
        self._scene.update()

    def load_state(self, state: Dict):
        self.setTransform(QtGui.QTransform(*state['transform']))
        self.centerOn(QtCore.QPointF(*state['center']))
        self.setSceneRect(QtCore.QRectF(*state['scene_rect']))
        
        for rect_data in state['rectangles']:
            rect_item = QtWidgets.QGraphicsRectItem(QtCore.QRectF(*rect_data), self._photo)
            rect_item.setBrush(QtGui.QBrush(QtGui.QColor(255, 192, 203, 100)))  # Transparent pink
            self._rectangles.append(rect_item)

    def save_state(self):
        transform = self.transform()
        center = self.mapToScene(self.viewport().rect().center())
        return {
            'rectangles': [rect.rect().getRect() for rect in self._rectangles],
            'transform': (
                transform.m11(), transform.m12(), transform.m13(),
                transform.m21(), transform.m22(), transform.m23(),
                transform.m31(), transform.m32(), transform.m33()
            ),
            'center': (center.x(), center.y()),
            'scene_rect': (self.sceneRect().x(), self.sceneRect().y(), 
                           self.sceneRect().width(), self.sceneRect().height())
        }

    def create_inpaint_cursor(self, cursor_type, size):
        from PySide6.QtGui import QPixmap, QPainter, QBrush, QColor, QCursor
        from PySide6.QtCore import Qt

        # Ensure size is at least 1 pixel
        size = max(1, size//2)
        
        pixmap = QPixmap(size//2, size//2)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)

        if cursor_type == "brush":
            painter.setBrush(QBrush(QColor(255, 0, 0, 127)))  # Red color with some transparency
            painter.setPen(Qt.PenStyle.NoPen)
        elif cursor_type == "eraser":
            painter.setBrush(QBrush(QColor(0, 0, 0, 0)))  # Fully transparent
            painter.setPen(QColor(0, 0, 0, 127))  # Outline with some transparency
        else:
            painter.setBrush(QBrush(QColor(0, 0, 0, 127)))  # Default to some color if unknown type
            painter.setPen(Qt.PenStyle.NoPen)

        painter.drawEllipse(0, 0, (size - 1)//2, (size - 1)//2)
        painter.end()

        return QCursor(pixmap, size // 4, size // 4)
    
    def set_brush_size(self, size):
        self._brush_size = size
        self._brush_cursor = self.create_inpaint_cursor("brush", size)
        if self._current_tool == "brush":
            self.setCursor(self._brush_cursor)

    def set_eraser_size(self, size):
        self._eraser_size = size
        self._eraser_cursor = self.create_inpaint_cursor("eraser", size)
        if self._current_tool == "eraser":
            self.setCursor(self._eraser_cursor)
