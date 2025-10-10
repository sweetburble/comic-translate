import numpy as np
import tempfile
from PySide6.QtGui import QUndoCommand
import imkit as imk


class SetImageCommand(QUndoCommand):
    def __init__(self, parent, file_path: str, img_array: np.ndarray, 
                 display: bool = True):
        super().__init__()
        self.ct = parent
        self.update_image_history(file_path, img_array)
        self.first = True
        self.display_first_time = display

    def redo(self):
        if self.first:
            if not self.display_first_time:
                return
            
            file_path = self.ct.image_files[self.ct.curr_img_idx]
            
            # Ensure the file has proper history initialization
            if file_path not in self.ct.current_history_index:
                self.ct.current_history_index[file_path] = 0
            if file_path not in self.ct.image_history:
                self.ct.image_history[file_path] = [file_path]
                
            current_index = self.ct.current_history_index[file_path]
            img_array = self.get_img(file_path, current_index)
            self.ct.image_viewer.display_image_array(img_array)
            self.first = False

        if self.ct.curr_img_idx >= 0:
            file_path = self.ct.image_files[self.ct.curr_img_idx]
            
            # Ensure proper initialization
            if file_path not in self.ct.current_history_index:
                self.ct.current_history_index[file_path] = 0
            if file_path not in self.ct.image_history:
                self.ct.image_history[file_path] = [file_path]
                
            current_index = self.ct.current_history_index[file_path]
            
            if current_index < len(self.ct.image_history[file_path]) - 1:
                current_index += 1
                self.ct.current_history_index[file_path] = current_index

                img_array = self.get_img(file_path, current_index)

                self.ct.image_data[file_path] = img_array
                self.ct.image_viewer.display_image_array(img_array)

    def undo(self):
        if self.ct.curr_img_idx >= 0:

            file_path = self.ct.image_files[self.ct.curr_img_idx]
            
            # Ensure proper initialization
            if file_path not in self.ct.current_history_index:
                self.ct.current_history_index[file_path] = 0
            if file_path not in self.ct.image_history:
                self.ct.image_history[file_path] = [file_path]
                
            current_index = self.ct.current_history_index[file_path]
            
            if current_index > 0:
                current_index -= 1
                self.ct.current_history_index[file_path] = current_index
                
                img_array = self.get_img(file_path, current_index)

                self.ct.image_data[file_path] = img_array
                self.ct.image_viewer.display_image_array(img_array)

   
    def update_image_history(self, file_path: str, img_array: np.ndarray):
        im = self.ct.load_image(file_path)

        if not np.array_equal(im, img_array):
            self.ct.image_data[file_path] = img_array
            
            # Update file path history
            history = self.ct.image_history[file_path]
            current_index = self.ct.current_history_index[file_path]
            
            # Remove any future history if we're not at the end
            del history[current_index + 1:]
            
            # # Save new image to temp file and add to history
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png', dir=self.ct.temp_dir)
            imk.write_image(temp_file.name, img_array)
            temp_file.close()

            history.append(temp_file.name)

            # Update in-memory history if this image is loaded
            if self.ct.in_memory_history.get(file_path, []):
                in_mem_history = self.ct.in_memory_history[file_path]
                del in_mem_history[current_index + 1:]
                in_mem_history.append(img_array.copy())

            self.ct.current_history_index[file_path] = len(history) - 1

    def get_img(self, file_path, current_index):
        if self.ct.in_memory_history.get(file_path, []):
            img_array = self.ct.in_memory_history[file_path][current_index]
        else:
            img_array = imk.read_image(self.ct.image_history[file_path][current_index])

        return img_array