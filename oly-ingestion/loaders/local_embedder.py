# loaders/local_embedder.py
"""
Local (open-weight) embeddings via sentence-transformers — the `local`
provider in loaders/embedders.py.

CPU is enough for this corpus: the 4,607-chunk dev copy embeds in minutes on
Qwen3-Embedding-0.6B and a query takes tens of milliseconds. Anything
sentence-transformers can load works; models that want an instruction on the
query side (Qwen3-Embedding) are listed in QUERY_PROMPTS. Vectors are fitted
to the column width by `fit_dimension` like every other provider.
"""

import logging

from loaders.embedders import Embedder, fit_dimension

logger = logging.getLogger(__name__)

# model-name substring → sentence-transformers prompt_name for query-time encoding
QUERY_PROMPTS: dict[str, str] = {
    "Qwen3-Embedding": "query",
}


class LocalEmbedder(Embedder):
    provider = "local"

    def __init__(self, model_name: str, dim: int, device: str | None = None, batch_size: int = 32):
        from sentence_transformers import SentenceTransformer

        super().__init__(model_name, dim)
        self.batch_size = batch_size
        self.model = SentenceTransformer(model_name, device=device or "cpu", trust_remote_code=True)
        native = self.model.get_sentence_embedding_dimension()
        self.native_dim = int(native) if native else dim
        self.query_prompt = next((p for key, p in QUERY_PROMPTS.items() if key in model_name), None)
        if self.native_dim != dim:
            logger.info(f"{model_name}: native dim {self.native_dim} → {'padded' if self.native_dim < dim else 'truncated'} to {dim}")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return fit_dimension(self.model.encode(texts, batch_size=self.batch_size, normalize_embeddings=False,
                                               show_progress_bar=False), self.dim)

    def embed_query(self, text: str) -> list[float]:
        kwargs = {"prompt_name": self.query_prompt} if self.query_prompt else {}
        return fit_dimension(self.model.encode([text], normalize_embeddings=False, show_progress_bar=False, **kwargs), self.dim)[0]
