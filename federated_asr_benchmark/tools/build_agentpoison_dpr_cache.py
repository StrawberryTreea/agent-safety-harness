from __future__ import annotations

import argparse
import json
from pathlib import Path
import pickle
from typing import Any


MODEL_ID = "facebook/dpr-ctx_encoder-single-nq-base"


def build_cache(*, agentpoison_root: Path, python_batch_size: int) -> dict[str, Any]:
    import torch
    from tqdm import tqdm
    from transformers import AutoTokenizer, DPRContextEncoder

    if not torch.cuda.is_available():
        raise RuntimeError("AgentPoison official DPR cache requires CUDA")
    database_root = agentpoison_root / "ReAct" / "database"
    corpus_path = database_root / "strategyqa_train_paragraphs.json"
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    texts = [row["content"] for row in corpus.values()]
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    model = DPRContextEncoder.from_pretrained(MODEL_ID).to("cuda").eval()
    embeddings: list[list[list[float]]] = []
    with torch.inference_mode():
        for start in tqdm(range(0, len(texts), python_batch_size), desc="DPR corpus"):
            encoded = tokenizer(
                texts[start : start + python_batch_size],
                return_tensors="pt",
                padding="max_length",
                truncation=True,
                max_length=512,
            )
            encoded = {name: value.to("cuda") for name, value in encoded.items()}
            rows = model(**encoded).pooler_output.detach().cpu().tolist()
            embeddings.extend([[row] for row in rows])

    output_dir = database_root / "embeddings"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "strategyqa_database_embeddings_dpr-ctx_encoder-single-nq-base.pkl"
    temporary = output_path.with_name(output_path.name + ".tmp")
    with temporary.open("wb") as handle:
        pickle.dump(embeddings, handle, protocol=pickle.HIGHEST_PROTOCOL)
    temporary.replace(output_path)
    metadata = {
        "schema_version": "0.1",
        "model_id": MODEL_ID,
        "corpus_path": str(corpus_path.resolve()),
        "record_count": len(embeddings),
        "embedding_shape": [len(embeddings), 1, len(embeddings[0][0])],
        "dtype": "float32",
        "cuda_device": torch.cuda.get_device_name(0),
        "torch_version": torch.__version__,
        "output_path": str(output_path.resolve()),
    }
    metadata_path = output_path.with_suffix(".metadata.json")
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the official AgentPoison DPR corpus cache.")
    parser.add_argument("--agentpoison-root", required=True, type=Path)
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()
    if args.batch_size <= 0:
        parser.error("--batch-size must be positive")
    metadata = build_cache(
        agentpoison_root=args.agentpoison_root.resolve(),
        python_batch_size=args.batch_size,
    )
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
