import os
import logging
from pathlib import Path
from typing import Callable, List, Optional, Union

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_BGE_MODEL = None
_BGE_MODEL_META = {}
DEFAULT_MODEL_ID = "BAAI/bge-m3"
DEFAULT_LOCAL_DIR = Path("model") / "BAAI__bge-m3"


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def _auto_pick_device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _resolve_model_source(model_name: Optional[str]) -> str:
    """
    优先使用本地 model 目录中的 BGE 模型；不存在时回退到 HF model id。
    """
    if model_name:
        return model_name
    if DEFAULT_LOCAL_DIR.exists():
        return str(DEFAULT_LOCAL_DIR.resolve())
    return DEFAULT_MODEL_ID


def _get_or_load_bge_model(
    model_source: str,
    device: str,
    cache_dir: str,
    max_seq_length: Optional[int] = None,
):
    global _BGE_MODEL, _BGE_MODEL_META

    model_key = (model_source, device, str(Path(cache_dir).resolve()))
    if _BGE_MODEL is not None and _BGE_MODEL_META.get("key") == model_key:
        return _BGE_MODEL

    from sentence_transformers import SentenceTransformer

    source_is_local_dir = Path(model_source).exists()
    if not source_is_local_dir:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)

    logger.info(
        "Loading BGE embedding model: source=%s (device=%s, cache_dir=%s)",
        model_source,
        device,
        cache_dir,
    )

    model_kwargs = {
        "model_name_or_path": model_source,
        "device": device,
        "trust_remote_code": True,
    }
    if not source_is_local_dir:
        model_kwargs["cache_folder"] = cache_dir

    model = SentenceTransformer(**model_kwargs)
    if max_seq_length and max_seq_length > 0:
        model.max_seq_length = max_seq_length

    _BGE_MODEL = model
    _BGE_MODEL_META = {"key": model_key}
    return _BGE_MODEL


def get_embed_model(
    model_name: Optional[str] = None,
    device: Optional[str] = None,
    cache_dir: Optional[str] = None,
    normalize_embeddings: bool = True,
    require_cuda: Optional[bool] = None,
) -> Callable[[Union[str, List[str]]], Union[List[float], List[List[float]]]]:
    resolved_device = (device or os.getenv("EMBED_MODEL_DEVICE", "auto")).strip().lower()
    if resolved_device == "auto":
        resolved_device = _auto_pick_device()

    if require_cuda is None:
        require_cuda = _truthy(os.getenv("EMBED_REQUIRE_CUDA", "0"))

    if require_cuda:
        try:
            import torch
            if not torch.cuda.is_available():
                raise RuntimeError(
                    "EMBED_REQUIRE_CUDA=1 but torch.cuda.is_available() is False."
                )
        except Exception as exc:
            raise RuntimeError("CUDA is required for embedding but unavailable.") from exc
        if resolved_device != "cuda":
            raise RuntimeError(
                f"CUDA is required for embedding, but resolved device is '{resolved_device}'. "
                "Set EMBED_MODEL_DEVICE=cuda or pass device='cuda'."
            )

    resolved_cache_dir = cache_dir or os.getenv("EMBED_MODEL_CACHE_DIR", "model")
    max_seq_env = os.getenv("BGE_M3_MAX_SEQ_LENGTH", "").strip()
    max_seq_length = int(max_seq_env) if max_seq_env.isdigit() else None
    model_source = _resolve_model_source(model_name)

    model = _get_or_load_bge_model(
        model_source=model_source,
        device=resolved_device,
        cache_dir=resolved_cache_dir,
        max_seq_length=max_seq_length,
    )

    batch_size_env = os.getenv("EMBED_BATCH_SIZE", "").strip()
    batch_size = int(batch_size_env) if batch_size_env.isdigit() else 32

    def embed_model(text: Union[str, List[str]]) -> Union[List[float], List[List[float]]]:
        if isinstance(text, str):
            query = text.strip()
            if not query:
                return []
            vector = model.encode(
                [query],
                normalize_embeddings=normalize_embeddings,
                convert_to_numpy=True,
                batch_size=1,
                show_progress_bar=False,
            )[0]
            return vector.tolist()

        if isinstance(text, list):
            cleaned = [str(t).strip() for t in text]
            if not cleaned:
                return []
            vectors = model.encode(
                cleaned,
                normalize_embeddings=normalize_embeddings,
                convert_to_numpy=True,
                batch_size=batch_size,
                show_progress_bar=False,
            )
            return vectors.tolist()

        raise TypeError(f"Unsupported input type for embed_model: {type(text)}")

    return embed_model
