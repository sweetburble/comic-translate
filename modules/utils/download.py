import os, sys, hashlib
import logging
from enum import Enum
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Dict, List, Union
from .download_file import download_url_to_file

logger = logging.getLogger(__name__)

# Paths / Globals
current_file_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_file_dir, '..', '..'))
models_base_dir = os.path.join(project_root, 'models')


_download_event_callback: Optional[Callable[[str, str], None]] = None

def set_download_callback(callback: Callable[[str, str], None]):
    """Register a global callback to be notified of model download events.

    Args:
        callback: Callable(status: str, name: str)
    """
    global _download_event_callback
    _download_event_callback = callback

def notify_download_event(status: str, name: str):
    """Notify subscribers about a download event without hard dependency on UI."""
    try:
        if _download_event_callback:
            _download_event_callback(status, name)
    except Exception:
        # Never allow UI notification failures to break downloads
        pass


def calculate_sha256_checksum(file_path: str) -> str:
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def calculate_md5_checksum(file_path: str) -> str:
    md5_hash = hashlib.md5()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            md5_hash.update(byte_block)
    return md5_hash.hexdigest()


class ModelID(Enum):
    MANGA_OCR_BASE = "manga-ocr-base"
    MANGA_OCR_BASE_ONNX = "manga-ocr-base-onnx"
    PORORO = "pororo"
    PORORO_ONNX = "pororo-onnx"
    LAMA_ONNX = "lama-manga-dynamic"
    AOT_JIT = "aot-traced"
    AOT_ONNX = "aot-onnx"
    LAMA_JIT = "anime-manga-big-lama"
    MIGAN_PIPELINE_ONNX = "migan-pipeline-v2"
    MIGAN_ONNX = "migan-onnx"
    MIGAN_JIT = "migan-traced"
    RTDETRV2_ONNX = "rtdetr-v2-onnx"


@dataclass(frozen=True)
class ModelSpec:
    id: ModelID
    url: str
    files: List[str]
    sha256: List[Optional[str]]
    save_dir: str

    def as_legacy_dict(self) -> Dict[str, Union[str, List[str]]]:
        """Return a dict shaped like the old module-level *_data objects."""
        return {
            'url': self.url,
            'files': list(self.files),
            'sha256_pre_calculated': list(self.sha256),
            'save_dir': self.save_dir,
        }


class ModelDownloader:
    """Central registry & download helper for model assets."""

    registry: Dict[ModelID, ModelSpec] = {}

    @classmethod
    def register(cls, spec: ModelSpec):
        cls.registry[spec.id] = spec

    @classmethod
    def get(cls, model: Union[ModelID, ModelSpec]):
        spec = cls.registry[model] if isinstance(model, ModelID) else model
        _download_spec(spec)

    @classmethod
    def ensure(cls, models: Iterable[Union[ModelID, ModelSpec]]):
        for m in models:
            cls.get(m)

    # Path Helpers
    @classmethod
    def file_paths(cls, model: Union[ModelID, ModelSpec]) -> List[str]:
        """Ensure model is present then return absolute paths to all its files."""
        spec = cls.registry[model] if isinstance(model, ModelID) else model
        cls.get(spec.id)  # ensure downloaded
        return [os.path.join(spec.save_dir, f) for f in spec.files]

    @classmethod
    def primary_path(cls, model: Union[ModelID, ModelSpec]) -> str:
        """Return the first file path for a model (common for single-file specs)."""
        return cls.file_paths(model)[0]

    @classmethod
    def get_file_path(cls, model: Union[ModelID, ModelSpec], file_name: str) -> str:
        """Ensure model is present then return the absolute path for the requested file_name.

        Raises ValueError if the file_name is not part of the model spec.
        """
        spec = cls.registry[model] if isinstance(model, ModelID) else model
        # ensure downloaded
        cls.get(spec.id)
        if file_name not in spec.files:
            raise ValueError(f"File '{file_name}' is not declared for model {spec.id}")
        return os.path.join(spec.save_dir, file_name)

    @classmethod
    def file_path_map(cls, model: Union[ModelID, ModelSpec]) -> Dict[str, str]:
        """Return a dict mapping each declared filename to its absolute path (ensures download)."""
        spec = cls.registry[model] if isinstance(model, ModelID) else model
        cls.get(spec.id)
        return {f: os.path.join(spec.save_dir, f) for f in spec.files}

    @classmethod
    def is_downloaded(cls, model: Union[ModelID, ModelSpec]) -> bool:
        """Return True if all files for the model exist and match provided checksums (when present)."""
        spec = cls.registry[model] if isinstance(model, ModelID) else model
        for file_name, expected_checksum in zip(spec.files, spec.sha256):
            file_path = os.path.join(spec.save_dir, file_name)
            if not os.path.exists(file_path):
                return False
            if expected_checksum:
                # verify checksum by detecting algorithm via length
                try:
                    if len(expected_checksum) == 64:
                        calc = calculate_sha256_checksum(file_path)
                    elif len(expected_checksum) == 32:
                        calc = calculate_md5_checksum(file_path)
                    else:
                        # unknown checksum format, skip verification
                        continue
                except Exception:
                    return False
                if calc != expected_checksum:
                    return False
        return True


# Core download implementations (shared)

def _download_single_file(file_url: str, file_path: str, expected_checksum: Optional[str]):
    sys.stderr.write(f'Downloading: "{file_url}" to {os.path.dirname(file_path)}\n')
    notify_download_event('start', os.path.basename(file_path))
    download_url_to_file(file_url, file_path, hash_prefix=None, progress=True)
    notify_download_event('end', os.path.basename(file_path))

    if expected_checksum:
        # Detect hash algorithm via length: 64=sha256, 32=md5
        if len(expected_checksum) == 64:
            algo = 'sha256'
            calculated_checksum = calculate_sha256_checksum(file_path)
        elif len(expected_checksum) == 32:
            algo = 'md5'
            calculated_checksum = calculate_md5_checksum(file_path)
        else:
            logger.warning(f"Unknown checksum length for {file_path} (len={len(expected_checksum)}). Skipping verification.")
            return

        if calculated_checksum == expected_checksum:
            logger.info(f"Download model success, {algo}: {calculated_checksum}")
        else:
            try:
                os.remove(file_path)
                logger.error(
                    f"Model {algo}: {calculated_checksum}, expected {algo}: {expected_checksum}, wrong model deleted. Please restart comic-translate."
                )
            except Exception:
                logger.error(
                    f"Model {algo}: {calculated_checksum}, expected {algo}: {expected_checksum}, please delete {file_path} and restart comic-translate."
                )
            raise RuntimeError(
                f"Model {algo} mismatch for {file_path}: got {calculated_checksum}, expected {expected_checksum}. "
                "Please delete the file and restart comic-translate or re-download the model."
            )


def _download_spec(spec: ModelSpec):
    if not os.path.exists(spec.save_dir):
        os.makedirs(spec.save_dir, exist_ok=True)
        print(f"Created directory: {spec.save_dir}")

    for file_name, expected_checksum in zip(spec.files, spec.sha256):
        file_url = f"{spec.url}{file_name}"
        file_path = os.path.join(spec.save_dir, file_name)

        if os.path.exists(file_path) and expected_checksum:
            calculated = calculate_sha256_checksum(file_path)
            if calculated == expected_checksum:
                continue
            else:
                print(
                    f"Checksum mismatch for {file_name}. Expected {expected_checksum}, got {calculated}. Redownloading..."
                )

        _download_single_file(file_url, file_path, expected_checksum)


# Registry population
def _register_defaults():
    ModelDownloader.register(ModelSpec(
        id=ModelID.MANGA_OCR_BASE,
        url='https://huggingface.co/kha-white/manga-ocr-base/resolve/main/',
        files=[
            'pytorch_model.bin', 'config.json', 'preprocessor_config.json',
            'README.md', 'special_tokens_map.json', 'tokenizer_config.json', 'vocab.txt'
        ],
        sha256=[
            'c63e0bb5b3ff798c5991de18a8e0956c7ee6d1563aca6729029815eda6f5c2eb',
            '8c0e395de8fa699daaac21aee33a4ba9bd1309cfbff03147813d2a025f39f349',
            'af4eb4d79cf61b47010fc0bc9352ee967579c417423b4917188d809b7e048948',
            '32f413afcc4295151e77d25202c5c5d81ef621b46f947da1c3bde13256dc0d5f',
            '303df45a03609e4ead04bc3dc1536d0ab19b5358db685b6f3da123d05ec200e3',
            'd775ad1deac162dc56b84e9b8638f95ed8a1f263d0f56f4f40834e26e205e266',
            '344fbb6b8bf18c57839e924e2c9365434697e0227fac00b88bb4899b78aa594d'
        ],
        save_dir=os.path.join(models_base_dir, 'ocr', 'manga-ocr-base')
    ))

    ModelDownloader.register(ModelSpec(
        id=ModelID.MANGA_OCR_BASE_ONNX,
        url='https://huggingface.co/mayocream/manga-ocr-onnx/resolve/main/',
        files=['encoder_model.onnx', 'decoder_model.onnx', 'vocab.txt'],
        sha256=[
            '15fa8155fe9bc1a7d25d9bb353debaa4def033d0174e907dbd2dd6d995def85f',
            'ef7765261e9d1cdc34d89356986c2bbc2a082897f753a89605ae80fdfa61f5e8',
            '5cb5c5586d98a2f331d9f8828e4586479b0611bfba5d8c3b6dadffc84d6a36a3',
        ],
        save_dir=os.path.join(models_base_dir, 'ocr', 'manga-ocr-base-onnx')
    ))

    ModelDownloader.register(ModelSpec(
        id=ModelID.PORORO,
        url='https://huggingface.co/ogkalu/pororo/resolve/main/',
        files=['craft.pt', 'brainocr.pt', 'ocr-opt.txt'],
        sha256=[
            '4a5efbfb48b4081100544e75e1e2b57f8de3d84f213004b14b85fd4b3748db17',
            '125820ba8ae4fa5d9fd8b8a2d4d4a7afe96a70c32b1aa01d4129001a6f61baec',
            'dd471474e91d78e54b179333439fea58158ad1a605df010ea0936dcf4387a8c2'
        ],
        save_dir=os.path.join(models_base_dir, 'ocr', 'pororo')
    ))

    ModelDownloader.register(ModelSpec(
        id=ModelID.PORORO_ONNX,
        url='https://huggingface.co/ogkalu/pororo/resolve/main/',
        files=['craft.onnx', 'brainocr.onnx', 'ocr-opt.txt'],
        sha256=[
            'e87cbb40ecb3c881971dea378ead9f80d2d607a011ccb4ca161f27823ed438ca',
            '25369c7dbeaed126dc5adb9f97134003b2d7fa7257861e0a4d90b5c5b2343d69',
            'dd471474e91d78e54b179333439fea58158ad1a605df010ea0936dcf4387a8c2'
        ],
        save_dir=os.path.join(models_base_dir, 'ocr', 'pororo-onnx')
    ))

    # Inpainting: LaMa ONNX (single file)
    ModelDownloader.register(ModelSpec(
        id=ModelID.LAMA_ONNX,
        url='https://huggingface.co/ogkalu/lama-manga-onnx-dynamic/resolve/main/',
        files=['lama-manga-dynamic.onnx'],
        sha256=['de31ffa5ba26916b8ea35319f6c12151ff9654d4261bccf0583a69bb095315f9'],
        save_dir=os.path.join(models_base_dir, 'inpainting')
    ))

    # Inpainting: MIGAN pipeline ONNX (single file)
    ModelDownloader.register(ModelSpec(
        id=ModelID.MIGAN_PIPELINE_ONNX,
        url='https://github.com/Sanster/models/releases/download/migan/',
        files=['migan_pipeline_v2.onnx'],
        sha256=[None],  # GitHub release no sha256 provided; could be added later
        save_dir=os.path.join(models_base_dir, 'inpainting')
    ))

    # Inpainting: AOT traced TorchScript
    ModelDownloader.register(ModelSpec(
        id=ModelID.AOT_JIT,
        url='https://huggingface.co/ogkalu/aot-inpainting/resolve/main/',
        files=['aot_traced.pt'],
        sha256=['5ecdac562c1d56267468fc4fbf80db27'], # md5
        save_dir=os.path.join(models_base_dir, 'inpainting')
    ))

    # Inpainting: AOT ONNX
    ModelDownloader.register(ModelSpec(
        id=ModelID.AOT_ONNX,
        url='https://huggingface.co/ogkalu/aot-inpainting/resolve/main/',
        files=['aot.onnx'],
        sha256=['ffd39ed8e2a275869d3b49180d030f0d8b8b9c2c20ed0e099ecd207201f0eada'],
        save_dir=os.path.join(models_base_dir, 'inpainting')
    ))

    # Inpainting: LaMa JIT (TorchScript)
    ModelDownloader.register(ModelSpec(
        id=ModelID.LAMA_JIT,
        url='https://github.com/Sanster/models/releases/download/AnimeMangaInpainting/',
        files=['anime-manga-big-lama.pt'],
        sha256=['29f284f36a0a510bcacf39ecf4c4d54f'],  # md5
        save_dir=os.path.join(models_base_dir, 'inpainting')
    ))

    # Inpainting: MIGAN traced JIT (TorchScript)
    ModelDownloader.register(ModelSpec(
        id=ModelID.MIGAN_JIT,
        url='https://github.com/Sanster/models/releases/download/migan/',
        files=['migan_traced.pt'],
        sha256=['76eb3b1a71c400ee3290524f7a11b89c'],  # md5
        save_dir=os.path.join(models_base_dir, 'inpainting')
    ))

    ModelDownloader.register(ModelSpec(
        id=ModelID.MIGAN_ONNX,
        url='',
        files=['migan.onnx'],
        sha256=[''],  # md5
        save_dir=os.path.join(models_base_dir, 'inpainting')
    ))

    ModelDownloader.register(ModelSpec(
        id=ModelID.RTDETRV2_ONNX,
        url='https://huggingface.co/ogkalu/comic-text-and-bubble-detector/resolve/main/',
        files=['detector.onnx'],
        sha256=['065744e91c0594ad8663aa8b870ce3fb27222942eded5a3cc388ce23421bd195'], 
        save_dir=os.path.join(models_base_dir, 'detection')
    ))

_register_defaults()

# List of models that should always be ensured at startup (can be ModelID items)
mandatory_models: List[Union[ModelID, ModelSpec, Dict[str, Union[str, List[str]]]]] = []

# Utility to normalize mixed mandatory_models entries at startup
def ensure_mandatory_models():
    for m in mandatory_models:
        if isinstance(m, ModelSpec):
            ModelDownloader.get(m)
        else:  # Enum
            ModelDownloader.get(m)
