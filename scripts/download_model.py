#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import os
from pathlib import Path

from _bootstrap import bootstrap_project


bootstrap_project()

from m_agent.paths import MODEL_DIR


def _default_local_dir(model_id: str) -> Path:
    safe_name = model_id.replace("/", "__")
    return MODEL_DIR / safe_name


def download_hf_model(
    model_id: str,
    local_dir: Path,
    force_download: bool = False,
    hf_endpoint: str | None = None,
) -> Path:
    from huggingface_hub import snapshot_download

    local_dir.mkdir(parents=True, exist_ok=True)
    if hf_endpoint:
        os.environ["HF_ENDPOINT"] = hf_endpoint

    snapshot_download(
        repo_id=model_id,
        local_dir=str(local_dir),
        local_dir_use_symlinks=False,
        force_download=force_download,
        resume_download=not force_download,
    )
    return local_dir


def _auto_pick_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def verify_bge_model(local_dir: Path, device: str = "auto", require_cuda: bool = False) -> None:
    from sentence_transformers import SentenceTransformer
    import torch

    resolved_device = _auto_pick_device() if device == "auto" else device
    if require_cuda and not torch.cuda.is_available():
        raise RuntimeError("require-cuda is set but torch.cuda.is_available() is False")
    if require_cuda and resolved_device != "cuda":
        raise RuntimeError(f"require-cuda is set but resolved device is '{resolved_device}'")

    model = SentenceTransformer(str(local_dir), trust_remote_code=True, device=resolved_device)
    vec = model.encode(["hello"], convert_to_numpy=True, show_progress_bar=False)
    print(
        f"[OK] Model load verified, embedding shape: {vec.shape}, "
        f"device={resolved_device}, cuda_available={torch.cuda.is_available()}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Download embedding model to local model directory.")
    parser.add_argument("--model-id", default="BAAI/bge-m3", help="Hugging Face model id")
    parser.add_argument(
        "--local-dir",
        default=None,
        help="Local directory to store model (default: model/<model-id with / replaced by __>)",
    )
    parser.add_argument("--force", action="store_true", help="Force re-download")
    parser.add_argument(
        "--hf-endpoint",
        default=None,
        help="Custom Hugging Face endpoint, e.g. https://hf-mirror.com",
    )
    parser.add_argument("--verify", action="store_true", help="Verify model can be loaded after download")
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"], help="Device for verify")
    parser.add_argument("--require-cuda", action="store_true", help="Fail verify if CUDA is unavailable")
    args = parser.parse_args()

    target_dir = Path(args.local_dir) if args.local_dir else _default_local_dir(args.model_id)
    target_dir = target_dir.resolve()

    print(f"[INFO] Downloading model: {args.model_id}")
    print(f"[INFO] Target directory: {target_dir}")
    if args.hf_endpoint:
        print(f"[INFO] Using HF endpoint: {args.hf_endpoint}")

    out_dir = download_hf_model(
        model_id=args.model_id,
        local_dir=target_dir,
        force_download=args.force,
        hf_endpoint=args.hf_endpoint,
    )

    print(f"[DONE] Model downloaded to: {out_dir}")

    if args.verify:
        verify_bge_model(out_dir, device=args.device, require_cuda=args.require_cuda)


if __name__ == "__main__":
    main()
