from __future__ import annotations

import hashlib
import math
import re
import time
from typing import Any

from pymilvus import MilvusClient

from .database import Database
from .tools import KnowledgeTools, ToolValidationError


class HashEmbeddingProvider:
    """Deterministic, key-free embedding for infrastructure tests and demo data."""

    def __init__(self, dimension: int):
        self.dimension = dimension

    def embed(self, text: str) -> list[float]:
        normalized = text.lower().strip()
        latin = re.findall(r"[a-z0-9_-]+", normalized)
        chinese = [char for char in normalized if "\u4e00" <= char <= "\u9fff"]
        bigrams = ["".join(chinese[index : index + 2]) for index in range(len(chinese) - 1)]
        tokens = latin + chinese + bigrams
        vector = [0.0] * self.dimension
        for token in tokens or [normalized]:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class HybridReranker:
    """Fuse dense Milvus scores and lexical scores into an explainable final rank."""

    def __init__(self, dense_weight: float = 0.65, lexical_weight: float = 0.35):
        self.dense_weight = dense_weight
        self.lexical_weight = lexical_weight

    def rank(
        self,
        documents: dict[str, dict[str, Any]],
        dense_scores: dict[str, float],
        lexical_scores: dict[str, float],
        top_k: int,
    ) -> list[dict[str, Any]]:
        ranked = []
        for doc_id in set(dense_scores) | set(lexical_scores):
            doc = documents.get(doc_id)
            if not doc:
                continue
            dense = max(0.0, min(1.0, dense_scores.get(doc_id, 0.0)))
            lexical = max(0.0, min(1.0, lexical_scores.get(doc_id, 0.0)))
            score = self.dense_weight * dense + self.lexical_weight * lexical
            ranked.append((score, dense, lexical, doc))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "id": doc["id"],
                "title": doc["title"],
                "section": doc["section"],
                "content": doc["content"],
                "version": doc["version"],
                "score": round(score, 3),
                "retrieval": {
                    "backend": "milvus-hybrid",
                    "dense_score": round(dense, 3),
                    "lexical_score": round(lexical, 3),
                },
            }
            for score, dense, lexical, doc in ranked[:top_k]
        ]


class MilvusKnowledgeTools:
    def __init__(
        self,
        database: Database,
        uri: str,
        collection: str,
        dimension: int = 128,
        token: str | None = None,
        fail_open: bool = True,
    ):
        self.database = database
        self.collection = collection
        self.embedding = HashEmbeddingProvider(dimension)
        self.reranker = HybridReranker()
        self.fallback = KnowledgeTools(database)
        self.fail_open = fail_open
        options: dict[str, Any] = {"uri": uri}
        if token:
            options["token"] = token
        self.client = MilvusClient(**options)
        self._ensure_collection(dimension)

    def _ensure_collection(self, dimension: int) -> None:
        if not self.client.has_collection(collection_name=self.collection):
            self.client.create_collection(
                collection_name=self.collection,
                dimension=dimension,
                metric_type="COSINE",
                consistency_level="Strong",
            )
        docs = self.database.fetch_all("SELECT * FROM knowledge_docs")
        data = []
        for doc in docs:
            numeric_id = int.from_bytes(hashlib.sha256(doc["id"].encode()).digest()[:7], "big")
            text = f"{doc['title']} {doc['section']} {doc['content']} {doc['tags']}"
            data.append(
                {
                    "id": numeric_id,
                    "vector": self.embedding.embed(text),
                    "doc_id": doc["id"],
                    "title": doc["title"],
                    "section": doc["section"],
                    "content": doc["content"],
                    "tags": doc["tags"],
                    "version": doc["version"],
                }
            )
        if data:
            self.client.upsert(collection_name=self.collection, data=data)

    def search(self, run_id: str, query: str, top_k: int = 4) -> list[dict[str, Any]]:
        if not 1 <= top_k <= 8:
            raise ToolValidationError("top_k 必须在 1..8")
        started = time.perf_counter()
        try:
            docs_list = self.database.fetch_all("SELECT * FROM knowledge_docs")
            documents = {doc["id"]: doc for doc in docs_list}
            response = self.client.search(
                collection_name=self.collection,
                data=[self.embedding.embed(query)],
                limit=min(len(documents), top_k * 3),
                output_fields=["doc_id"],
                search_params={"metric_type": "COSINE", "params": {}},
            )
            dense_scores: dict[str, float] = {}
            for hit in response[0] if response else []:
                entity = hit.get("entity", {})
                doc_id = entity.get("doc_id") or hit.get("doc_id")
                if doc_id:
                    dense_scores[str(doc_id)] = (float(hit.get("distance", 0.0)) + 1.0) / 2.0
            keywords = KnowledgeTools._keywords(query)
            lexical_raw = []
            for doc in docs_list:
                haystack = f"{doc['title']} {doc['section']} {doc['content']} {doc['tags']}".lower()
                lexical_raw.append((sum(keyword in haystack for keyword in keywords), doc["id"]))
            maximum = max((score for score, _ in lexical_raw), default=1) or 1
            lexical_scores = {doc_id: score / maximum for score, doc_id in lexical_raw if score}
            result = self.reranker.rank(documents, dense_scores, lexical_scores, top_k)
            self.database.audit(
                run_id, "RCAAgent", "knowledge-mcp", "milvus_hybrid_search",
                {"query": query[:200], "top_k": top_k, "collection": self.collection},
                f"success:{len(result)}", int((time.perf_counter() - started) * 1000),
            )
            return result
        except Exception as exc:
            if not self.fail_open:
                raise
            self.database.audit(
                run_id, "RCAAgent", "knowledge-mcp", "milvus_hybrid_search",
                {"query": query[:200], "top_k": top_k},
                f"degraded:{type(exc).__name__}", int((time.perf_counter() - started) * 1000),
            )
            return self.fallback.search(run_id, query, top_k)


def create_knowledge_tools(database: Database, settings: Any):
    if settings.knowledge_backend == "milvus":
        return MilvusKnowledgeTools(
            database=database,
            uri=settings.milvus_uri,
            token=settings.milvus_token,
            collection=settings.milvus_collection,
            dimension=settings.milvus_dimension,
            fail_open=settings.milvus_fail_open,
        )
    return KnowledgeTools(database)
