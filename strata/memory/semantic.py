"""
@module memory.semantic
@purpose Provide long-term semantic persistence (RAG) for task memories and code patterns.
@owns vector storage (ChromaDB), embedding generation, semantic retrieval
@does_not_own specific LLM inference, database models (SQL)
@key_exports SemanticMemory
@side_effects initiates vector DB database creation and storage
"""

import chromadb
from typing import List, Dict, Any, Optional

class SemanticMemory:
    """
    @summary Manages the vector memory (ChromaDB) for the swarm.
    @inputs persist_directory: where to save vectors
    @outputs side-effect driven (vector storage initialization)
    @side_effects writes to 'memory' directory on disk
    @depends chromadb
    @invariants always returns a list (empty if no matches).
    """
    def __init__(self, persist_directory: str = "memory/vector_db"):
        """
        @summary Initialize ChromaDB client.
        @inputs persist_directory: storage path
        @outputs none
        """
        self.client = chromadb.PersistentClient(path=persist_directory)
        self.collection = self.client.get_or_create_collection(name="swarm_memories")

    def upsert_task_memory(self, task_id: str, content: str, metadata: Dict[str, Any] = None):
        """
        @summary Index a finished task or synthesis result for future retrieval.
        @inputs task_id, content: the text to index, metadata: optional dict
        @outputs none
        @side_effects persists to vector store
        """
        print(f"Indexing memory for task: {task_id}...")
        self.collection.upsert(
            documents=[content],
            metadatas=[metadata or {}],
            ids=[task_id]
        )

    def query_memory(self, query: str, n_results: int = 3) -> List[Dict[str, Any]]:
        """
        @summary Semantic (similarity-based) search across past swarm memories.
        @inputs query: the semantic search term, n_results: top k matches
        @outputs list of relevant documents and their metadata
        @side_effects performs vector similarity calculations
        """
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results
        )
        return results
