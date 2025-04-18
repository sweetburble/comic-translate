from typing import Any, Dict
import requests
import numpy as np
import json

from .base import BaseLLMTranslation
from ...utils.translator_utils import MODEL_MAP
from .sys_prompt import get_prefill  # 프리필 함수 임포트 추가


class ClaudeTranslation(BaseLLMTranslation):
    """Translation engine using Anthropic Claude models via direct REST API calls."""
    
    def __init__(self):
        """Initialize Claude translation engine."""
        super().__init__()
        self.model_name = None
        self.api_key = None
        self.api_url = "https://api.anthropic.com/v1/messages"
        self.headers = None
    
    def initialize(self, settings: Any, source_lang: str, target_lang: str, model_name: str, **kwargs) -> None:
        """
        Initialize Claude translation engine.
        
        Args:
            settings: Settings object with credentials
            source_lang: Source language name
            target_lang: Target language name
            model_name: Claude model name
        """
        super().initialize(settings, source_lang, target_lang, **kwargs)
        
        self.temperature = self.temperature/2
        self.model_name = model_name
        credentials = settings.get_credentials(settings.ui.tr('Anthropic Claude'))
        self.api_key = credentials.get('api_key', '')
        
        # Set up headers for API requests
        self.headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        }
        
        self.model = MODEL_MAP.get(self.model_name)
    
    def _perform_translation(self, user_prompt: str, system_prompt: str, image: np.ndarray) -> str:
        # 프리필 텍스트 가져오기
        prefill_text = get_prefill()
        
        # Prepare request payload
        payload: Dict[str, Any] = {
            "model": self.model,
            "system": system_prompt,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
        }
        
        # 사용자 메시지 구성
        user_message = {"role": "user", "content": []}
        
        if self.img_as_llm_input and image is not None:
            # Use the encode_image method from the base class
            encoded_image, media_type = self.encode_image(image)
            
            user_message["content"] = [
                {"type": "text", "text": user_prompt}, 
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": encoded_image}}
            ]
        else:
            user_message["content"] = [
                {"type": "text", "text": user_prompt}
            ]
        
        # 프리필 텍스트를 assistant 메시지로 추가
        assistant_message = {
            "role": "assistant",
            "content": prefill_text
        }
        
        # 메시지 배열에 사용자 메시지와 프리필 메시지 추가
        payload["messages"] = [user_message, assistant_message]

        # Make the API request
        response = requests.post(
            self.api_url,
            headers=self.headers,
            data=json.dumps(payload)
        )
        
        # Handle response
        if response.status_code == 200:
            response_data = response.json()
            return response_data['content'][0]['text']
        else:
            error_msg = f"Error {response.status_code}: {response.text}"
            raise Exception(f"Claude API request failed: {error_msg}")
