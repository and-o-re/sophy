"""
RAG Local com ChromaDB e Embeddings via Ollama (`nomic-embed-text`) - FASE 3.
Indexa semanticamente todas as notas Markdown do Córtex Neural no Obsidian
(`OBSIDIAN_VAULT_PATH`) em chunks otimizados e permite busca vetorial híbrida
100% offline.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

import chromadb
import ollama

from config import settings


class LocalVaultRAG:
    """Gerencia a coleção vetorial persistente no ChromaDB usando `nomic-embed-text`."""

    COLLECTION_NAME = "obsidian_cortex_rag"

    def __init__(self) -> None:
        settings.ensure_directories()
        self.chroma_client = chromadb.PersistentClient(path=str(settings.CHROMA_DB_PATH))
        self.collection = self.chroma_client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        self.ollama_client = ollama.Client(host=settings.OLLAMA_BASE_URL)

    def _embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Gera vetores de embedding localmente via `nomic-embed-text` no Ollama em lotes seguros."""
        all_embeddings: List[List[float]] = []
        batch_size = 16
        safe_texts = [t[:2200] for t in texts]
        for i in range(0, len(safe_texts), batch_size):
            batch = safe_texts[i : i + batch_size]
            response = self.ollama_client.embed(
                model=settings.OLLAMA_EMBED_MODEL,
                input=batch,
                keep_alive=settings.OLLAMA_KEEP_ALIVE,
            )
            all_embeddings.extend(response["embeddings"])
        return all_embeddings

    @staticmethod
    def _chunk_markdown(text: str, relative_path: str, max_chars: int = 900) -> List[Dict[str, Any]]:
        """
        Divide uma nota Markdown em blocos semânticos (por seções/parágrafos)
        respeitando `max_chars` para não sobrecarregar o contexto de 4096 tokens.
        """
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        chunks: List[Dict[str, Any]] = []
        current_buf: List[str] = []
        current_len = 0
        chunk_idx = 0

        for para in paragraphs:
            if current_len + len(para) > max_chars and current_buf:
                joined = "\n\n".join(current_buf)
                chunk_id = hashlib.sha1(f"{relative_path}::{chunk_idx}".encode("utf-8")).hexdigest()
                chunks.append(
                    {
                        "id": chunk_id,
                        "document": f"[Nota: {relative_path}]\n{joined}",
                        "metadata": {
                            "source_note": relative_path,
                            "chunk_index": chunk_idx,
                        },
                    }
                )
                chunk_idx += 1
                current_buf = [para]
                current_len = len(para)
            else:
                current_buf.append(para)
                current_len += len(para)

        if current_buf:
            joined = "\n\n".join(current_buf)
            chunk_id = hashlib.sha1(f"{relative_path}::{chunk_idx}".encode("utf-8")).hexdigest()
            chunks.append(
                {
                    "id": chunk_id,
                    "document": f"[Nota: {relative_path}]\n{joined}",
                    "metadata": {
                        "source_note": relative_path,
                        "chunk_index": chunk_idx,
                    },
                }
            )

        return chunks

    def index_vault(self) -> Dict[str, Any]:
        """Varre o `OBSIDIAN_VAULT_PATH`, gera embeddings com `nomic-embed-text` e faz upsert no ChromaDB."""
        vault_root = settings.OBSIDIAN_VAULT_PATH.resolve()
        all_chunks: List[Dict[str, Any]] = []
        notes_count = 0

        for md_file in sorted(vault_root.rglob("*.md")):
            rel_path = str(md_file.relative_to(vault_root))
            if rel_path.startswith("Local AI/") or rel_path.startswith("01_Skills/awesome-claude-skills/"):
                continue
            try:
                content = md_file.read_text(encoding="utf-8").strip()
            except Exception:
                continue
            if not content:
                continue

            notes_count += 1
            all_chunks.extend(self._chunk_markdown(content, rel_path))

        if not all_chunks:
            return {"status": "EMPTY_VAULT", "notes_indexed": 0, "chunks_indexed": 0}

        ids = [c["id"] for c in all_chunks]
        docs = [c["document"] for c in all_chunks]
        metas = [c["metadata"] for c in all_chunks]
        embeddings = self._embed_texts(docs)

        self.collection.upsert(
            ids=ids,
            documents=docs,
            metadatas=metas,
            embeddings=embeddings,
        )

        return {
            "status": "INDEXED",
            "embedding_model": settings.OLLAMA_EMBED_MODEL,
            "notes_indexed": notes_count,
            "chunks_indexed": len(all_chunks),
            "chroma_collection_total": self.collection.count(),
        }

    def query_semantic(self, query_text: str, top_k: int = 4) -> Dict[str, Any]:
        """Realiza busca semântica vetorial no ChromaDB usando `nomic-embed-text`."""
        if self.collection.count() == 0:
            self.index_vault()

        if self.collection.count() == 0:
            return {"status": "EMPTY_INDEX", "results": []}

        q_emb = self._embed_texts([query_text])[0]
        effective_k = min(max(1, top_k), self.collection.count())

        res = self.collection.query(
            query_embeddings=[q_emb],
            n_results=effective_k,
            include=["documents", "metadatas", "distances"],
        )

        matches = []
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]

        for doc, meta, dist in zip(docs, metas, dists):
            similarity = round(1.0 - float(dist), 4)
            matches.append(
                {
                    "source_note": meta.get("source_note"),
                    "chunk_index": meta.get("chunk_index"),
                    "cosine_similarity": similarity,
                    "content": doc,
                }
            )

        return {
            "status": "OK",
            "query": query_text,
            "embedding_model": settings.OLLAMA_EMBED_MODEL,
            "matches": matches,
        }


_rag_engine: LocalVaultRAG | None = None


def _get_rag() -> LocalVaultRAG:
    global _rag_engine
    if _rag_engine is None:
        _rag_engine = LocalVaultRAG()
    return _rag_engine


# =====================================================================
# TOOLS EXPOSTAS AO AGENTE (RAG COM CHROMADB + NOMIC-EMBED-TEXT)
# =====================================================================

def index_obsidian_to_rag() -> str:
    """
    Indexa (ou re-indexa) todas as notas Markdown do Vault do Obsidian no banco
    vetorial local ChromaDB utilizando o modelo `nomic-embed-text` do Ollama.
    Use esta ferramenta após criar notas importantes ou quando o usuário pedir
    para atualizar a memória vetorial / RAG.
    """
    result = _get_rag().index_vault()
    return json.dumps(result, ensure_ascii=False, indent=2)


def query_rag_knowledge(query: str, top_k: int = 4) -> str:
    """
    Realiza uma busca semântica vetorial (RAG) no ChromaDB usando `nomic-embed-text`
    sobre todo o conhecimento armazenado no Obsidian.
    Retorna os trechos mais relevantes ordenados por similaridade de cosseno.
    """
    result = _get_rag().query_semantic(query_text=query, top_k=top_k)
    return json.dumps(result, ensure_ascii=False, indent=2)


RAG_TOOLS = [
    index_obsidian_to_rag,
    query_rag_knowledge,
]
