from typing import List
from functools import lru_cache

from sentence_transformers import SentenceTransformer

from chunking.character_splitter import Chunk
from config.settings import PROJECT_ROOT


@lru_cache(maxsize=1)
def load_embedding_model(model_name: str) -> SentenceTransformer:
    """
    Load and return the SentenceTransformer embedding model.
    Called once at ingest time and once at query time.
    """
    return SentenceTransformer(model_name, cache_folder=str(PROJECT_ROOT / ".cache" / "huggingface" / "hub"))


def embed_chunks(chunks: List[Chunk], model: SentenceTransformer) -> List[List[float]]:
    """
    Embed a list of Chunk objects and return a list of embedding vectors.
    Each vector corresponds to the Chunk at the same index.
    Prints embedding progress: 'Embedding X chunks with model Y...'
    """
    model_name = getattr(getattr(model, 'tokenizer', None), 'name_or_path', 'SentenceTransformer')
    print(f"Embedding {len(chunks)} chunks with model {model_name}...")
    texts = [chunk.content for chunk in chunks]
    embeddings = model.encode(texts, show_progress_bar=False)
    return [emb.tolist() for emb in embeddings]
