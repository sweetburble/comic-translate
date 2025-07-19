import os, json
import cv2, shutil
import numpy as np
import requests
import logging
import hashlib
from datetime import datetime
from typing import List
from PySide6 import QtCore
from PySide6.QtGui import QColor

from modules.detection.processor import TextBlockDetector
from modules.ocr.processor import OCRProcessor
from modules.translation.processor import Translator
from modules.utils.textblock import TextBlock, sort_blk_list
from modules.utils.pipeline_utils import inpaint_map, get_config
from modules.rendering.render import get_best_render_area, pyside_word_wrap
from modules.utils.pipeline_utils import generate_mask, get_language_code, is_directory_empty
from modules.utils.translator_utils import get_raw_translation, get_raw_text, format_translations, set_upper_case
from modules.utils.archives import make

from app.ui.canvas.rectangle import MoveableRectItem
from app.ui.canvas.text_item import OutlineInfo, OutlineType
from app.ui.canvas.save_renderer import ImageSaveRenderer


logger = logging.getLogger(__name__)

class ComicTranslatePipeline:
    def __init__(self, main_page):
        self.main_page = main_page
        self.block_detector_cache = None
        self.inpainter_cache = None
        self.cached_inpainter_key = None
        self.ocr = OCRProcessor()
        self.ocr_cache = {} # OCR results cache: {(image_hash, model_key, source_lang): {block_id: text}}
        self.translation_cache = {} # Translation results cache: {(image_hash, translator_key, source_lang, target_lang, extra_context): {block_id: translation}}

    def clear_ocr_cache(self):
        """Clear the OCR cache. Note: Cache now persists across image and model changes automatically."""
        self.ocr_cache = {}
        logger.info("OCR cache manually cleared")

    def clear_translation_cache(self):
        """Clear the translation cache. Note: Cache now persists across image and model changes automatically."""
        self.translation_cache = {}
        logger.info("Translation cache manually cleared")

    def load_box_coords(self, blk_list: List[TextBlock]):
        self.main_page.image_viewer.clear_rectangles()
        if self.main_page.image_viewer.hasPhoto() and blk_list:
            for blk in blk_list:
                x1, y1, x2, y2 = blk.xyxy
                rect = QtCore.QRectF(0, 0, x2 - x1, y2 - y1)
                rect_item = MoveableRectItem(rect, self.main_page.image_viewer.photo)
                if blk.tr_origin_point:
                    rect_item.setTransformOriginPoint(QtCore.QPointF(*blk.tr_origin_point))
                rect_item.setPos(x1,y1)
                rect_item.setRotation(blk.angle)
                self.main_page.connect_rect_item_signals(rect_item)
                self.main_page.image_viewer.rectangles.append(rect_item)

            rect = self.main_page.rect_item_ctrl.find_corresponding_rect(self.main_page.blk_list[0], 0.5)
            self.main_page.image_viewer.select_rectangle(rect)
            self.main_page.set_tool('box')

    def detect_blocks(self, load_rects=True):
        if self.main_page.image_viewer.hasPhoto():
            if self.block_detector_cache is None:
                self.block_detector_cache = TextBlockDetector(self.main_page.settings_page)
            image = self.main_page.image_viewer.get_cv2_image()
            blk_list = self.block_detector_cache.detect(image)

            return blk_list, load_rects

    def on_blk_detect_complete(self, result): 
        blk_list, load_rects = result
        source_lang = self.main_page.s_combo.currentText()
        source_lang_english = self.main_page.lang_mapping.get(source_lang, source_lang)
        rtl = True if source_lang_english == 'Japanese' else False
        blk_list = sort_blk_list(blk_list, rtl)
        self.main_page.blk_list = blk_list
        if load_rects:
            self.load_box_coords(blk_list)


    def manual_inpaint(self):
        image_viewer = self.main_page.image_viewer
        settings_page = self.main_page.settings_page
        mask = image_viewer.get_mask_for_inpainting()
        image = image_viewer.get_cv2_image()

        if self.inpainter_cache is None or self.cached_inpainter_key != settings_page.get_tool_selection('inpainter'):
            device = 'cuda' if settings_page.is_gpu_enabled() else 'cpu'
            inpainter_key = settings_page.get_tool_selection('inpainter')
            InpainterClass = inpaint_map[inpainter_key]
            self.inpainter_cache = InpainterClass(device)
            self.cached_inpainter_key = inpainter_key

        config = get_config(settings_page)
        inpaint_input_img = self.inpainter_cache(image, mask, config)
        inpaint_input_img = cv2.convertScaleAbs(inpaint_input_img) 

        return inpaint_input_img

    def inpaint_complete(self, patch_list):
        self.main_page.apply_inpaint_patches(patch_list)
        self.main_page.image_viewer.clear_brush_strokes() 
        self.main_page.undo_group.activeStack().endMacro()  
        # get_best_render_area(self.main_page.blk_list, original_image, inpainted)    

    def get_inpainted_patches(self, mask: np.ndarray, inpainted_image: np.ndarray):
        # slice mask into bounding boxes
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                    cv2.CHAIN_APPROX_SIMPLE)
        patches = []
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            patch = inpainted_image[y:y+h, x:x+w]
            patches.append({
                'bbox': (x, y, w, h),
                'cv2_img': patch.copy(),
            })

        return patches
    
    def inpaint(self):
        mask = self.main_page.image_viewer.get_mask_for_inpainting()
        painted = self.manual_inpaint()              
        patches = self.get_inpainted_patches(mask, painted)
        return patches         
    
    def get_selected_block(self):
        rect = self.main_page.image_viewer.selected_rect
        srect = rect.mapRectToScene(rect.rect())
        srect_coords = srect.getCoords()
        blk = self.main_page.rect_item_ctrl.find_corresponding_text_block(srect_coords)
        return blk

    def _generate_image_hash(self, image):
        """Generate a hash for the image to use as cache key"""
        try:
            # Use a small portion of the image data to generate hash for efficiency
            # Take every 10th pixel to reduce computation while maintaining uniqueness
            sample_data = image[::10, ::10].tobytes()
            return hashlib.md5(sample_data).hexdigest()
        except Exception as e:
            # Fallback: use the full image shape and first few bytes if sampling fails
            shape_str = str(image.shape) if hasattr(image, 'shape') else str(type(image))
            fallback_data = shape_str.encode() + str(image.dtype).encode() if hasattr(image, 'dtype') else b'fallback'
            return hashlib.md5(fallback_data).hexdigest()

    def _get_cache_key(self, image, source_lang):
        """Generate cache key for OCR results"""
        image_hash = self._generate_image_hash(image)
        ocr_model = self.main_page.settings_page.get_tool_selection('ocr')
        return (image_hash, ocr_model, source_lang)

    def _get_block_id(self, block):
        """Generate a unique identifier for a text block based on its position"""
        # Use the block's bounding box coordinates as a unique identifier
        try:
            x1, y1, x2, y2 = block.xyxy
            return f"{x1}_{y1}_{x2}_{y2}"
        except (AttributeError, ValueError, TypeError):
            # Fallback: use object id if xyxy is not available or malformed
            return str(id(block))

    def _is_ocr_cached(self, cache_key):
        """Check if OCR results are cached for this image/model/language combination"""
        return cache_key in self.ocr_cache

    def _cache_ocr_results(self, cache_key, blk_list):
        """Cache OCR results for all blocks"""
        try:
            block_results = {}
            for blk in blk_list:
                block_id = self._get_block_id(blk)
                # Ensure we have text to cache, use empty string if None
                text = getattr(blk, 'text', '') or ''
                block_results[block_id] = text
            self.ocr_cache[cache_key] = block_results
        except Exception as e:
            logger.warning(f"Failed to cache OCR results: {e}")
            # Don't raise exception, just skip caching

    def _get_cached_text_for_block(self, cache_key, block):
        """Retrieve cached text for a specific block"""
        block_id = self._get_block_id(block)
        cached_results = self.ocr_cache.get(cache_key, {})
        return cached_results.get(block_id, "")

    def _get_translation_cache_key(self, image, source_lang, target_lang, translator_key, extra_context):
        """Generate cache key for translation results"""
        image_hash = self._generate_image_hash(image)
        # Include extra_context in cache key since it affects translation results
        context_hash = hashlib.md5(extra_context.encode()).hexdigest() if extra_context else "no_context"
        return (image_hash, translator_key, source_lang, target_lang, context_hash)

    def _is_translation_cached(self, cache_key):
        """Check if translation results are cached for this image/translator/language combination"""
        return cache_key in self.translation_cache

    def _cache_translation_results(self, cache_key, blk_list):
        """Cache translation results for all blocks"""
        try:
            block_results = {}
            for blk in blk_list:
                block_id = self._get_block_id(blk)
                # Ensure we have translation to cache, use empty string if None
                translation = getattr(blk, 'translation', '') or ''
                block_results[block_id] = translation
            self.translation_cache[cache_key] = block_results
        except Exception as e:
            logger.warning(f"Failed to cache translation results: {e}")
            # Don't raise exception, just skip caching

    def _get_cached_translation_for_block(self, cache_key, block):
        """Retrieve cached translation for a specific block"""
        block_id = self._get_block_id(block)
        cached_results = self.translation_cache.get(cache_key, {})
        return cached_results.get(block_id, "")

    def OCR_image(self, single_block=False):
        source_lang = self.main_page.s_combo.currentText()
        if self.main_page.image_viewer.hasPhoto() and self.main_page.image_viewer.rectangles:
            image = self.main_page.image_viewer.get_cv2_image()
            cache_key = self._get_cache_key(image, source_lang)
            
            if single_block:
                blk = self.get_selected_block()
                if blk is None:
                    return
                
                # Check if we have cached results for this image/model/language
                if self._is_ocr_cached(cache_key):
                    cached_text = self._get_cached_text_for_block(cache_key, blk)
                    blk.text = cached_text
                    logger.info(f"Using cached OCR result for block: {cached_text}")
                else:
                    # Run OCR on a deep copies of all the blocks and cache the results
                    logger.info("No cached OCR results found, running OCR on entire page...")
                    self.ocr.initialize(self.main_page, source_lang)
                    all_blocks = [blk.deep_copy() for blk in self.main_page.blk_list]
                    
                    if all_blocks:  
                        self.ocr.process(image, all_blocks)
                        self._cache_ocr_results(cache_key, all_blocks)
                        cached_text = self._get_cached_text_for_block(cache_key, blk)
                        blk.text = cached_text
                        logger.info(f"Cached OCR results and extracted text for block: {cached_text}")
            else:
                # For full page OCR, run normally and cache results
                self.ocr.initialize(self.main_page, source_lang)
                if self.main_page.blk_list:  
                    self.ocr.process(image, self.main_page.blk_list)
                    self._cache_ocr_results(cache_key, self.main_page.blk_list)
                    logger.info("Block Length: %d", len(self.main_page.blk_list))

    def translate_image(self, single_block=False):
        source_lang = self.main_page.s_combo.currentText()
        target_lang = self.main_page.t_combo.currentText()
        if self.main_page.image_viewer.hasPhoto() and self.main_page.blk_list:
            settings_page = self.main_page.settings_page
            image = self.main_page.image_viewer.get_cv2_image()
            extra_context = settings_page.get_llm_settings()['extra_context']
            translator_key = settings_page.get_tool_selection('translator')

            upper_case = settings_page.ui.uppercase_checkbox.isChecked()

            translator = Translator(self.main_page, source_lang, target_lang)
            
            # Get translation cache key
            translation_cache_key = self._get_translation_cache_key(
                image, source_lang, target_lang, translator_key, extra_context
            )
            
            if single_block:
                blk = self.get_selected_block()
                if blk is None:
                    return
                
                # Check if we have cached translation results for this image/translator/language combination
                if self._is_translation_cached(translation_cache_key):
                    cached_translation = self._get_cached_translation_for_block(translation_cache_key, blk)
                    blk.translation = cached_translation
                    logger.info(f"Using cached translation result for block: {cached_translation}")
                else:
                    # Run translation on a deep copies of all the blocks and cache the results
                    logger.info("No cached translation results found, running translation on entire page...")
                    all_blocks = [blk.deep_copy() for blk in self.main_page.blk_list]
                    
                    if all_blocks:  
                        translator.translate(all_blocks, image, extra_context)
                        self._cache_translation_results(translation_cache_key, all_blocks)
                        cached_translation = self._get_cached_translation_for_block(translation_cache_key, blk)
                        blk.translation = cached_translation
                        logger.info(f"Cached translation results and extracted translation for block: {cached_translation}")
                
                set_upper_case([blk], upper_case)
            else:
                # For full page translation, run normally and cache results
                translator.translate(self.main_page.blk_list, image, extra_context)
                self._cache_translation_results(translation_cache_key, self.main_page.blk_list)
                set_upper_case(self.main_page.blk_list, upper_case)

    def skip_save(self, directory, timestamp, base_name, extension, archive_bname, image):
        path = os.path.join(directory, f"comic_translate_{timestamp}", "translated_images", archive_bname)
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)
        cv2.imwrite(os.path.join(path, f"{base_name}_translated{extension}"), image)

    def log_skipped_image(self, directory, timestamp, image_path, reason=""):
        skipped_file = os.path.join(directory, f"comic_translate_{timestamp}", "skipped_images.txt")
        with open(skipped_file, 'a', encoding='UTF-8') as file:
            file.write(image_path + "\n")
            file.write(reason + "\n\n")

    def batch_process(self, selected_paths: List[str] = None):
        timestamp = datetime.now().strftime("%b-%d-%Y_%I-%M-%S%p")
        image_list = selected_paths if selected_paths is not None else self.main_page.image_files
        total_images = len(image_list)

        for index, image_path in enumerate(image_list):

            file_on_display = self.main_page.image_files[self.main_page.curr_img_idx]
            if self.main_page.selected_batch:
                current_batch_file = self.main_page.selected_batch[index]
            else:
                current_batch_file = self.main_page.image_files[index]

            # index, step, total_steps, change_name
            self.main_page.progress_update.emit(index, total_images, 0, 10, True)

            settings_page = self.main_page.settings_page
            source_lang = self.main_page.image_states[image_path]['source_lang']
            target_lang = self.main_page.image_states[image_path]['target_lang']

            target_lang_en = self.main_page.lang_mapping.get(target_lang, None)
            trg_lng_cd = get_language_code(target_lang_en)
            
            base_name = os.path.splitext(os.path.basename(image_path))[0]
            extension = os.path.splitext(image_path)[1]
            directory = os.path.dirname(image_path)

            archive_bname = ""
            for archive in self.main_page.file_handler.archive_info:
                images = archive['extracted_images']
                archive_path = archive['archive_path']

                for img_pth in images:
                    if img_pth == image_path:
                        directory = os.path.dirname(archive_path)
                        archive_bname = os.path.splitext(os.path.basename(archive_path))[0]

            image = cv2.imread(image_path)

            # skip UI-skipped images
            state = self.main_page.image_states.get(image_path, {})
            if state.get('skip', False):
                self.skip_save(directory, timestamp, base_name, extension, archive_bname, image)
                self.log_skipped_image(directory, timestamp, image_path, "User-skipped")
                continue

            # Text Block Detection
            self.main_page.progress_update.emit(index, total_images, 1, 10, False)
            if self.main_page.current_worker and self.main_page.current_worker.is_cancelled:
                self.main_page.current_worker = None
                break

            if self.block_detector_cache is None:
                self.block_detector_cache = TextBlockDetector(self.main_page.settings_page)
            
            blk_list = self.block_detector_cache.detect(image)

            self.main_page.progress_update.emit(index, total_images, 2, 10, False)
            if self.main_page.current_worker and self.main_page.current_worker.is_cancelled:
                self.main_page.current_worker = None
                break

            if blk_list:
                self.ocr.initialize(self.main_page, source_lang)
                try:
                    self.ocr.process(image, blk_list)
                    source_lang_english = self.main_page.lang_mapping.get(source_lang, source_lang)
                    rtl = True if source_lang_english == 'Japanese' else False
                    blk_list = sort_blk_list(blk_list, rtl)
                    
                except Exception as e:
                    # if it's an HTTPError, try to pull the "error_description" field
                    if isinstance(e, requests.exceptions.HTTPError):
                        try:
                            err_json = e.response.json()
                            err_msg = err_json.get("error_description", str(e))
                        except Exception:
                            err_msg = str(e)
                    else:
                        err_msg = str(e)

                    logger.error(err_msg)
                    reason = f"OCR: {err_msg}"
                    self.skip_save(directory, timestamp, base_name, extension, archive_bname, image)
                    self.main_page.image_skipped.emit(image_path, "OCR", err_msg)
                    self.log_skipped_image(directory, timestamp, image_path, reason)
                    continue
            else:
                self.skip_save(directory, timestamp, base_name, extension, archive_bname, image)
                self.main_page.image_skipped.emit(image_path, "Text Blocks", "")
                self.log_skipped_image(directory, timestamp, image_path, "No text blocks detected")
                continue

            self.main_page.progress_update.emit(index, total_images, 3, 10, False)
            if self.main_page.current_worker and self.main_page.current_worker.is_cancelled:
                self.main_page.current_worker = None
                break

            # Clean Image of text
            export_settings = settings_page.get_export_settings()

            if self.inpainter_cache is None or self.cached_inpainter_key != settings_page.get_tool_selection('inpainter'):
                device = 'cuda' if settings_page.is_gpu_enabled() else 'cpu'
                inpainter_key = settings_page.get_tool_selection('inpainter')
                InpainterClass = inpaint_map[inpainter_key]
                self.inpainter_cache = InpainterClass(device)
                self.cached_inpainter_key = inpainter_key

            config = get_config(settings_page)
            mask = generate_mask(image, blk_list)

            self.main_page.progress_update.emit(index, total_images, 4, 10, False)
            if self.main_page.current_worker and self.main_page.current_worker.is_cancelled:
                self.main_page.current_worker = None
                break

            inpaint_input_img = self.inpainter_cache(image, mask, config)
            inpaint_input_img = cv2.convertScaleAbs(inpaint_input_img)

            # Saving cleaned image
            patches = self.get_inpainted_patches(mask, inpaint_input_img)
            self.main_page.patches_processed.emit(index, patches, image_path)

            inpaint_input_img = cv2.cvtColor(inpaint_input_img, cv2.COLOR_BGR2RGB)

            if export_settings['export_inpainted_image']:
                path = os.path.join(directory, f"comic_translate_{timestamp}", "cleaned_images", archive_bname)
                if not os.path.exists(path):
                    os.makedirs(path, exist_ok=True)
                cv2.imwrite(os.path.join(path, f"{base_name}_cleaned{extension}"), inpaint_input_img)

            self.main_page.progress_update.emit(index, total_images, 5, 10, False)
            if self.main_page.current_worker and self.main_page.current_worker.is_cancelled:
                self.main_page.current_worker = None
                break

            # Get Translations/ Export if selected
            extra_context = settings_page.get_llm_settings()['extra_context']
            translator_key = settings_page.get_tool_selection('translator')
            translator = Translator(self.main_page, source_lang, target_lang)
            
            # Get translation cache key for batch processing
            translation_cache_key = self._get_translation_cache_key(
                image, source_lang, target_lang, translator_key, extra_context
            )
            
            try:
                translator.translate(blk_list, image, extra_context)
                # Cache the translation results for potential future use
                self._cache_translation_results(translation_cache_key, blk_list)
            except Exception as e:
                # if it's an HTTPError, try to pull the "error_description" field
                if isinstance(e, requests.exceptions.HTTPError):
                    try:
                        err_json = e.response.json()
                        err_msg = err_json.get("error_description", str(e))
                    except Exception:
                        err_msg = str(e)
                else:
                    err_msg = str(e)

                logger.error(err_msg)
                reason = f"Translator: {err_msg}"
                self.skip_save(directory, timestamp, base_name, extension, archive_bname, image)
                self.main_page.image_skipped.emit(image_path, "Translator", err_msg)
                self.log_skipped_image(directory, timestamp, image_path, reason)
                continue

            entire_raw_text = get_raw_text(blk_list)
            entire_translated_text = get_raw_translation(blk_list)

            # Parse JSON strings and check if they're empty objects or invalid
            try:
                raw_text_obj = json.loads(entire_raw_text)
                translated_text_obj = json.loads(entire_translated_text)
                
                if (not raw_text_obj) or (not translated_text_obj):
                    self.skip_save(directory, timestamp, base_name, extension, archive_bname, image)
                    self.main_page.image_skipped.emit(image_path, "Translator", "")
                    self.log_skipped_image(directory, timestamp, image_path, "Translator: empty JSON")
                    continue
            except json.JSONDecodeError as e:
                # Handle invalid JSON
                error_message = str(e)
                reason = f"Translator: JSONDecodeError: {error_message}"
                self.skip_save(directory, timestamp, base_name, extension, archive_bname, image)
                self.main_page.image_skipped.emit(image_path, "Translator", error_message)
                self.log_skipped_image(directory, timestamp, image_path, reason)
                continue

            if export_settings['export_raw_text']:
                path = os.path.join(directory, f"comic_translate_{timestamp}", "raw_texts", archive_bname)
                if not os.path.exists(path):
                    os.makedirs(path, exist_ok=True)
                file = open(os.path.join(path, os.path.splitext(os.path.basename(image_path))[0] + "_raw.txt"), 'w', encoding='UTF-8')
                file.write(entire_raw_text)

            if export_settings['export_translated_text']:
                path = os.path.join(directory, f"comic_translate_{timestamp}", "translated_texts", archive_bname)
                if not os.path.exists(path):
                    os.makedirs(path, exist_ok=True)
                file = open(os.path.join(path, os.path.splitext(os.path.basename(image_path))[0] + "_translated.txt"), 'w', encoding='UTF-8')
                file.write(entire_translated_text)

            self.main_page.progress_update.emit(index, total_images, 7, 10, False)
            if self.main_page.current_worker and self.main_page.current_worker.is_cancelled:
                self.main_page.current_worker = None
                break

            # Text Rendering
            render_settings = self.main_page.render_settings()
            upper_case = render_settings.upper_case
            outline = render_settings.outline
            format_translations(blk_list, trg_lng_cd, upper_case=upper_case)
            get_best_render_area(blk_list, image, inpaint_input_img)

            font = render_settings.font_family
            font_color = QColor(render_settings.color)

            max_font_size = render_settings.max_font_size
            min_font_size = render_settings.min_font_size
            line_spacing = float(render_settings.line_spacing) 
            outline_width = float(render_settings.outline_width)
            outline_color = QColor(render_settings.outline_color) 
            bold = render_settings.bold
            italic = render_settings.italic
            underline = render_settings.underline
            alignment_id = render_settings.alignment_id
            alignment = self.main_page.button_to_alignment[alignment_id]
            direction = render_settings.direction
                
            text_items_state = []
            for blk in blk_list:
                x1, y1, width, height = blk.xywh

                translation = blk.translation
                if not translation or len(translation) == 1:
                    continue

                translation, font_size = pyside_word_wrap(translation, font, width, height,
                                                        line_spacing, outline_width, bold, italic, underline,
                                                        alignment, direction, max_font_size, min_font_size)
                
                # Display text if on current page  
                if current_batch_file == file_on_display:
                    self.main_page.blk_rendered.emit(translation, font_size, blk)

                if any(lang in trg_lng_cd.lower() for lang in ['zh', 'ja', 'th']):
                    translation = translation.replace(' ', '')

                text_items_state.append({
                'text': translation,
                'font_family': font,
                'font_size': font_size,
                'text_color': font_color,
                'alignment': alignment,
                'line_spacing': line_spacing,
                'outline_color': outline_color,
                'outline_width': outline_width,
                'bold': bold,
                'italic': italic,
                'underline': underline,
                'position': (x1, y1),
                'rotation': blk.angle,
                'scale': 1.0,
                'transform_origin': blk.tr_origin_point,
                'width': width,
                'direction': direction,
                'selection_outlines': [
                    OutlineInfo(0, len(translation), 
                    outline_color, 
                    outline_width, 
                    OutlineType.Full_Document)
                ] if outline else [],
                })

            self.main_page.image_states[image_path]['viewer_state'].update({
                'text_items_state': text_items_state
                })
            
            self.main_page.image_states[image_path]['viewer_state'].update({
                'push_to_stack': True
                })
            
            self.main_page.progress_update.emit(index, total_images, 9, 10, False)
            if self.main_page.current_worker and self.main_page.current_worker.is_cancelled:
                self.main_page.current_worker = None
                break

            # Saving blocks with texts to history
            self.main_page.image_states[image_path].update({
                'blk_list': blk_list                   
            })

            if current_batch_file == file_on_display:
                self.main_page.blk_list = blk_list
                
            render_save_dir = os.path.join(directory, f"comic_translate_{timestamp}", "translated_images", archive_bname)
            if not os.path.exists(render_save_dir):
                os.makedirs(render_save_dir, exist_ok=True)
            sv_pth = os.path.join(render_save_dir, f"{base_name}_translated{extension}")

            im = cv2.cvtColor(inpaint_input_img, cv2.COLOR_RGB2BGR)
            renderer = ImageSaveRenderer(im)
            viewer_state = self.main_page.image_states[image_path]['viewer_state']
            patches = self.main_page.image_patches.get(image_path, [])
            renderer.apply_patches(patches)
            renderer.add_state_to_image(viewer_state)
            renderer.save_image(sv_pth)

            self.main_page.progress_update.emit(index, total_images, 10, 10, False)

        archive_info_list = self.main_page.file_handler.archive_info
        if archive_info_list:
            save_as_settings = settings_page.get_export_settings()['save_as']
            for archive_index, archive in enumerate(archive_info_list):
                archive_index_input = total_images + archive_index

                self.main_page.progress_update.emit(archive_index_input, total_images, 1, 3, True)
                if self.main_page.current_worker and self.main_page.current_worker.is_cancelled:
                    self.main_page.current_worker = None
                    break

                archive_path = archive['archive_path']
                archive_ext = os.path.splitext(archive_path)[1]
                archive_bname = os.path.splitext(os.path.basename(archive_path))[0]
                archive_directory = os.path.dirname(archive_path)
                save_as_ext = f".{save_as_settings[archive_ext.lower()]}"

                save_dir = os.path.join(archive_directory, f"comic_translate_{timestamp}", "translated_images", archive_bname)
                check_from = os.path.join(archive_directory, f"comic_translate_{timestamp}")

                self.main_page.progress_update.emit(archive_index_input, total_images, 2, 3, True)
                if self.main_page.current_worker and self.main_page.current_worker.is_cancelled:
                    self.main_page.current_worker = None
                    break

                # Create the new archive
                output_base_name = f"{archive_bname}"
                make(save_as_ext=save_as_ext, input_dir=save_dir, 
                    output_dir=archive_directory, output_base_name=output_base_name)

                self.main_page.progress_update.emit(archive_index_input, total_images, 3, 3, True)
                if self.main_page.current_worker and self.main_page.current_worker.is_cancelled:
                    self.main_page.current_worker = None
                    break

                # Clean up temporary 
                if os.path.exists(save_dir):
                    shutil.rmtree(save_dir)
                # The temp dir is removed when closing the app

                if is_directory_empty(check_from):
                    shutil.rmtree(check_from)






