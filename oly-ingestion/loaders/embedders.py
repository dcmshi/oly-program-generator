# loaders/embedders.py
"""
Embedding providers behind one interface.

`VectorLoader` never talks to an embedding API directly; it holds an
`Embedder` from `make_embedder(settings)` and calls `embed_documents` /
`embed_query`. Providers (EMBEDDING_PROVIDER):

    openai         OpenAI's API (OPENAI_API_KEY); `dimensions=` for text-embedding-3-*
    openai_compat  any OpenAI-compatible /v1/embeddings endpoint — Together,
                   Ollama (http://localhost:11434/v1), text-embeddings-inference,
                   vLLM … (EMBEDDING_BASE_URL + EMBEDDING_API_KEY)
    local          a sentence-transformers model on this machine
                   (loaders/local_embedder.py; CPU is enough for this corpus)

Every provider returns vectors fitted to `settings.embedding_dim` — the width
of the `embedding vector(N)` column — by `fit_dimension`: unit-normalise, then
zero-pad a narrower vector or truncate a wider one (Matryoshka models keep
their leading dims) and re-normalise. Zero padding leaves cosine distance
unchanged, so a model with another native width needs no schema change.
`knowledge_chunks.embedding_model` records the model per row, so two spaces
never share a ranking; a switch is `reembed.py` + a golden/baseline rebuild.

Adding a provider: subclass `Embedder`, implement the two methods, register
it in `make_embedder`. Keep model-specific query instructions inside the
provider (see `LocalEmbedder.QUERY_PROMPTS`).
"""

import logging
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


def fit_dimension(vectors: list[list[float]], dim: int) -> list[list[float]]:
    """Unit-normalise and pad/truncate each vector to `dim`."""
    import numpy as np

    v = np.asarray(vectors, dtype=np.float32)
    if v.ndim == 1:
        v = v[None, :]
    if v.shape[1] > dim:
        v = v[:, :dim]
    norms = np.linalg.norm(v, axis=1, keepdims=True)
    v = v / np.where(norms == 0, 1.0, norms)
    if v.shape[1] < dim:
        v = np.concatenate([v, np.zeros((v.shape[0], dim - v.shape[1]), dtype=np.float32)], axis=1)
    return v.tolist()


class Embedder(ABC):
    """One embedding space. `model_name` is what knowledge_chunks.embedding_model records."""

    provider: str = ""

    def __init__(self, model_name: str, dim: int):
        self.model_name = model_name
        self.dim = dim

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]:
        """Query-side encoding; providers with a query instruction override this."""
        return self.embed_documents([text])[0]


class _OpenAIStyleEmbedder(Embedder):
    """Shared body for the OpenAI API and any OpenAI-compatible endpoint."""

    BATCH_SIZE = 100          # texts per call (OpenAI allows 2048; smaller keeps long chunks safe)
    MAX_ATTEMPTS = 3

    def __init__(self, model_name: str, dim: int, client, *, send_dimensions: bool):
        super().__init__(model_name, dim)
        self.client = client
        self.send_dimensions = send_dimensions

    def _kwargs(self) -> dict:
        return {"dimensions": self.dim} if self.send_dimensions else {}

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

        retryable = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)
        out: list[list[float]] = []
        for i in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[i:i + self.BATCH_SIZE]
            logger.info(f"  Embedding batch {i // self.BATCH_SIZE + 1} ({len(batch)} texts)")
            for attempt in range(self.MAX_ATTEMPTS):
                try:
                    response = self.client.embeddings.create(model=self.model_name, input=batch, **self._kwargs())
                    out.extend(item.embedding for item in response.data)
                    break
                except retryable as e:
                    if attempt < self.MAX_ATTEMPTS - 1:
                        wait = 2 ** attempt
                        logger.warning(f"  Embedding call failed ({type(e).__name__}), retrying in {wait}s...")
                        time.sleep(wait)
                    else:
                        raise
        return fit_dimension(out, self.dim) if out else out


class OpenAIEmbedder(_OpenAIStyleEmbedder):
    provider = "openai"

    def __init__(self, model_name: str, dim: int, api_key: str):
        from openai import OpenAI

        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for embeddings. Set it in .env or as an environment variable.")
        # text-embedding-3-* accept Matryoshka `dimensions`; older models reject it
        super().__init__(model_name, dim, OpenAI(api_key=api_key), send_dimensions=model_name.startswith("text-embedding-3"))


class OpenAICompatEmbedder(_OpenAIStyleEmbedder):
    """Any /v1/embeddings server that speaks the OpenAI shape."""

    provider = "openai_compat"

    def __init__(self, model_name: str, dim: int, base_url: str, api_key: str = ""):
        from openai import OpenAI

        if not base_url:
            raise ValueError("EMBEDDING_BASE_URL is required for EMBEDDING_PROVIDER=openai_compat")
        # `dimensions` is not portable across servers; vectors are fitted client-side instead
        super().__init__(model_name, dim, OpenAI(api_key=api_key or "none", base_url=base_url), send_dimensions=False)


def make_embedder(settings) -> Embedder:
    provider = (getattr(settings, "embedding_provider", "") or "openai").lower()
    model = settings.embedding_model
    dim = int(getattr(settings, "embedding_dim", 1536))
    if provider == "openai":
        return OpenAIEmbedder(model, dim, settings.openai_api_key)
    if provider == "openai_compat":
        return OpenAICompatEmbedder(model, dim, getattr(settings, "embedding_base_url", ""),
                                    getattr(settings, "embedding_api_key", ""))
    if provider == "local":
        from loaders.local_embedder import LocalEmbedder
        return LocalEmbedder(model, dim)
    raise ValueError(f"EMBEDDING_PROVIDER must be one of openai, openai_compat, local — got {provider!r}")
