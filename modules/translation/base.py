from abc import ABC, abstractmethod
from typing import Any
import numpy as np

from ..utils.textblock import TextBlock


class TranslationEngine(ABC):
    """
    Abstract base class for all translation engines.
    Defines common interface and utility methods.
    """
    
    @abstractmethod
    def initialize(self, settings: Any, source_lang: str, target_lang: str, **kwargs) -> None:
        """
        Initialize the translation engine with necessary parameters.
        
        Args:
            settings: Settings object with credentials
            source_lang: Source language name
            target_lang: Target language name
            **kwargs: Engine-specific initialization parameters
        """
        pass
    
    def get_language_code(self, language: str) -> str:
        """
        Get standardized language code from language name.
        
        Args:
            language: Language name
            
        Returns:
            Standardized language code
        """
        from ..utils.pipeline_utils import get_language_code
        return get_language_code(language)
    
    def preprocess_text(self, blk_text: str, source_lang_code: str) -> str:
        """
        PreProcess text based on language:
        - Remove spaces for Chinese and Japanese languages
        - Remove all newline/carriage-return characters
        - Keep original text for other languages (aside from the newline removal)
        
        Args:
            blk_text (str): The input text to process
            source_lang_code (str): Language code of the source text
        
        Returns:
            str: Processed text
        """
        # Remove newline and carriage‐return characters
        text = blk_text.replace('\r', '').replace('\n', '')

        source_lang_code = source_lang_code.lower()
        
        # 2) If Chinese/Japanese, also remove all spaces
        if 'zh' in source_lang_code or source_lang_code == 'ja':
            return text.replace(' ', '')
        # 3) Otherwise, return the text (with newlines already removed)
        else:
            return text


class TraditionalTranslation(TranslationEngine):
    """Base class for traditional translation engines (non-LLM)."""
    
    @abstractmethod
    def translate(self, blk_list: list[TextBlock]) -> list[TextBlock]:
        """
        Translate text blocks using non-LLM translators.
        
        Args:
            blk_list: List of TextBlock objects containing text to translate
            
        Returns:
            List of updated TextBlock objects with translations
        """
        pass

    def preprocess_language_code(self, lang_code: str) -> str:
        """
        Preprocess language codes to match the specific translation API requirements.
        By default, returns the original language code.
        
        Args:
            lang_code: The language code to preprocess
            
        Returns:
            Preprocessed language code supported by the translation API
        """
        return lang_code  # Default implementation just returns the original code


class LLMTranslation(TranslationEngine):
    """Base class for LLM-based translation engines."""
    
    @abstractmethod
    def translate(self, blk_list: list[TextBlock], image: np.ndarray, extra_context: str) -> list[TextBlock]:
        """
        Translate text blocks using LLM.
        
        Args:
            blk_list: List of TextBlock objects containing text to translate
            image: Image as numpy array (for context)
            extra_context: Additional context information for translation
            
        Returns:
            List of updated TextBlock objects with translations
        """
        pass
    