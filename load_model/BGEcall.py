import os
import logging
from pathlib import Path
from typing import Callable, List, Optional, Union

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_BGE_MODEL = None
_BGE_MODEL_META = {}


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


def _get_or_load_bge_model(
    model_name: str,
    device: str,
    cache_dir: str,
    max_seq_length: Optional[int] = None,
):
    global _BGE_MODEL, _BGE_MODEL_META

    model_key = (model_name, device, str(Path(cache_dir).resolve()))
    if _BGE_MODEL is not None and _BGE_MODEL_META.get("key") == model_key:
        return _BGE_MODEL

    from sentence_transformers import SentenceTransformer

    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    logger.info(
        "Loading local embedding model: %s (device=%s, cache_dir=%s)",
        model_name,
        device,
        cache_dir,
    )
    model = SentenceTransformer(
        model_name_or_path=model_name,
        device=device,
        cache_folder=cache_dir,
        trust_remote_code=True,
    )
    if max_seq_length and max_seq_length > 0:
        model.max_seq_length = max_seq_length

    _BGE_MODEL = model
    _BGE_MODEL_META = {"key": model_key}
    return _BGE_MODEL


def get_embed_model(
    model_name: str = "BAAI/bge-m3",
    device: Optional[str] = None,
    cache_dir: Optional[str] = None,
    normalize_embeddings: bool = True,
) -> Callable[[Union[str, List[str]]], Union[List[float], List[List[float]]]]:
    resolved_device = (device or os.getenv("EMBED_MODEL_DEVICE", "auto")).strip().lower()
    if resolved_device == "auto":
        resolved_device = _auto_pick_device()

    resolved_cache_dir = cache_dir or os.getenv("EMBED_MODEL_CACHE_DIR", "checkpoints/embeddings")
    max_seq_env = os.getenv("BGE_M3_MAX_SEQ_LENGTH", "").strip()
    max_seq_length = int(max_seq_env) if max_seq_env.isdigit() else None

    model = _get_or_load_bge_model(
        model_name=model_name,
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
