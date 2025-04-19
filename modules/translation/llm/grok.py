from typing import Any

import numpy as np
import requests
import json

from .base import BaseLLMTranslation
from ...utils.translator_utils import MODEL_MAP

class GrokTranslation(BaseLLMTranslation):
    """Translation engine using Grok AI models through direct REST API calls."""

    def __init__(self):
        super().__init__()
        self.model_name = None
        self.api_key = None
        self.api_base_url = "https://api.x.ai/v1"
        self.supports_images = True

    def initialize(self, settings: Any, source_lang: str, target_lang: str, model_name: str, **kwargs) -> None:
        """
        Initialize Grok translation engine.

        Args:
            settings: Settings object with credentials
            source_lang: Source language name
            target_lang: Target language name
            model_name: Grok model name
        """
        super().initialize(settings, source_lang, target_lang, **kwargs)
        self.model_name = model_name
        credentials = settings.get_credentials(settings.ui.tr('Grok'))
        self.api_key = credentials.get('api_key', '')
        self.model = MODEL_MAP.get(self.model_name)

    def _perform_translation(self, user_prompt: str, system_prompt: str, image: np.ndarray) -> str:
        """
        Perform translation using direct REST API calls to Grok.

        Args:
            user_prompt: Text prompt from user
            system_prompt: System instructions
            image: Image as numpy array (이 구현에서는 사용하지 않음)

        Returns:
            Translated text
        """
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        messages = []
        
        # 시스템 메시지 추가
        if system_prompt:
            messages.append({
                "role": "system",
                "content": system_prompt
            })
        
        # 사용자 메시지 추가
        messages.append({
            "role": "user",
            "content": user_prompt
        })

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
        }

        return self._make_api_request(payload, headers)

    def _make_api_request(self, payload, headers):
        """
        Make API request and process response
        """
        try:
            response = requests.post(
                f"{self.api_base_url}/chat/completions",
                headers=headers,
                data=json.dumps(payload)
            )
            
            response.raise_for_status()
            response_data = response.json()
            return response_data["choices"][0]["message"]["content"]
        except requests.exceptions.RequestException as e:
            error_msg = f"API request failed: {str(e)}"
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_details = e.response.json()
                    error_msg += f" - {json.dumps(error_details)}"
                except:
                    error_msg += f" - Status code: {e.response.status_code}"
            raise RuntimeError(error_msg)
