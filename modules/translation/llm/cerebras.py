from typing import Any
import numpy as np
import requests

from .base import BaseLLMTranslation
# from .sys_prompt import get_cerebras_prefill  # Cerebras용 프리필 함수 임포트

class CerebrasTranslation(BaseLLMTranslation):
    """Cerebras 모델의 REST API를 사용하는 번역 엔진입니다."""
    
    def __init__(self):
        super().__init__()
        self.model_name = None
        self.api_key = None
        self.api_base_url = "https://api.cerebras.ai/v1/chat/completions"
    
    def initialize(self, settings: Any, source_lang: str, target_lang: str, model_name: str, **kwargs) -> None:
        """
        Cerebras 번역 엔진을 초기화합니다.
        
        Args:
            settings: 인증 정보가 포함된 설정 객체
            source_lang: 소스 언어 이름
            target_lang: 타겟 언어 이름
            model_name: Cerebras 모델 이름
        """
        super().initialize(settings, source_lang, target_lang, **kwargs)
        
        self.model_name = model_name
        # 설정에서 'Cerebras' 이름으로 API 키, 사용할 모델 이름을 가져옵니다.
        credentials = settings.get_credentials(settings.ui.tr('Cerebras'))
        self.api_key = credentials.get('api_key', '')
        self.model = credentials.get('model', '')
    
    def _perform_translation(self, user_prompt: str, system_prompt: str, image: np.ndarray) -> str:
        """
        Cerebras REST API를 사용하여 번역을 수행합니다.
        
        Args:
            user_prompt: 모델에 전달할 프롬프트
            system_prompt: 모델에 대한 시스템 지침
            image: 이미지 데이터 (numpy 배열)
            
        Returns:
            모델이 번역한 텍스트
        """
        # Cerebras API는 현재 이미지 입력을 지원하지 않으므로, 관련 로직을 비활성화하고 예외 처리를 추가합니다.
        if self.img_as_llm_input and image is not None:
            raise ValueError("Cerebras API는 현재 채팅 완료에서 이미지 입력을 지원하지 않습니다.")

        # API 요청 헤더 설정
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        # 메시지 리스트 구성
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        
        # 사용자 메시지 추가
        messages.append({"role": "user", "content": user_prompt})
        
        # 프리필 텍스트를 어시스턴트의 첫 응답으로 추가
        # prefill_text = get_cerebras_prefill()
        # if prefill_text:
        #     messages.append({"role": "assistant", "content": prefill_text})

        # 요청 페이로드 생성
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_completion_tokens": self.max_tokens,
            "top_p": self.top_p,
            "stream": False  # 스트리밍 비활성화
        }
        
        # Cerebras API로 요청 전송
        response = requests.post(
            self.api_base_url, 
            headers=headers, 
            json=payload,
            timeout=30  # 30초 타임아웃 설정
        )
        
        # 응답 처리
        if response.status_code != 200:
            error_msg = f"API 요청이 상태 코드 {response.status_code}로 실패했습니다: {response.text}"
            raise Exception(error_msg)
        
        response_data = response.json()
        
        try:
            # 응답에서 번역된 텍스트 추출
            # 응답 구조: {"choices": [{"message": {"content": "..."}}]}
            result = response_data["choices"][0]["message"]["content"]
            return result
        except (KeyError, IndexError, TypeError) as e:
            raise Exception(f"API 응답 파싱에 실패했습니다: {str(e)}. 응답 내용: {response_data}")