from .base import OCREngine
from .microsoft_ocr import MicrosoftOCR
from .google_ocr import GoogleOCR
from .gpt_ocr import GPTOCR
from .paddle_ocr import PaddleOCREngine
from .manga_ocr.engine import MangaOCREngine
from .pororo.engine import PororoOCREngine
from .doctr_ocr import DocTROCREngine
from ..utils.pipeline_utils import language_codes
from ..utils.translator_utils import get_llm_client, MODEL_MAP

class OCREngineFactory:
    """Factory for creating appropriate OCR engines based on settings."""
    
    _engines = {}  # Cache of created engines
    
    @classmethod
    def create_engine(cls, settings, source_lang_english: str) -> OCREngine:
        """
        Create or retrieve an appropriate OCR engine based on settings.
        
        Args:
            settings: Settings object with OCR configuration
            source_lang_english: Source language in English
            
        Returns:
            Appropriate OCR engine instance
        """
        ocr_model = settings.get_tool_selection('ocr')
        is_microsoft = ocr_model == settings.ui.tr("Microsoft OCR")
        is_google = ocr_model == settings.ui.tr("Google Cloud Vision")
        is_gpt = 'GPT' in ocr_model
        
        # Create a cache key based on model and language
        cache_key = f"{ocr_model}_{source_lang_english}"
        
        # Return cached engine if available
        if cache_key in cls._engines:
            return cls._engines[cache_key]
        
        # Microsoft OCR
        if is_microsoft:
            credentials = settings.get_credentials(settings.ui.tr("Microsoft Azure"))
            engine = MicrosoftOCR()
            engine.initialize(
                api_key=credentials['api_key_ocr'],
                endpoint=credentials['endpoint']
            )
            cls._engines[cache_key] = engine
            return engine
        
        # Google OCR
        elif is_google:
            credentials = settings.get_credentials(settings.ui.tr("Google Cloud"))
            engine = GoogleOCR()
            engine.initialize(api_key=credentials['api_key'])
            cls._engines[cache_key] = engine
            return engine
        
        elif is_gpt:
            credentials = settings.get_credentials(settings.ui.tr("Open AI GPT"))
            gpt_client = get_llm_client('GPT', credentials['api_key'])
            engine = GPTOCR()
            engine.initialize(client=gpt_client, model=MODEL_MAP.get(ocr_model))
            cls._engines[cache_key] = engine
            return engine
        
        # Language-specific default OCR engines
        elif source_lang_english == "Japanese":
            device = 'cuda' if settings.is_gpu_enabled() else 'cpu'
            engine = MangaOCREngine()
            engine.initialize(device=device)
            cls._engines[cache_key] = engine
            return engine
        
        elif source_lang_english == "Korean":
            engine = PororoOCREngine()
            engine.initialize()
            cls._engines[cache_key] = engine
            return engine
        
        elif source_lang_english == "Chinese":
            engine = PaddleOCREngine()
            engine.initialize()
            cls._engines[cache_key] = engine
            return engine
        
        elif source_lang_english == "Russian":
            credentials = settings.get_credentials(settings.ui.tr("Open AI GPT"))
            gpt_client = get_llm_client('GPT', credentials['api_key'])
            engine = GPTOCR()
            engine.initialize(client=gpt_client, model='gpt-4o')
            cls._engines[cache_key] = engine
            return engine
        
        # Default to doctr for any other language
        else:
            device = 'cuda' if settings.is_gpu_enabled() else 'cpu'
            engine = DocTROCREngine()
            engine.initialize(device=device)
            cls._engines[cache_key] = engine
            return engine