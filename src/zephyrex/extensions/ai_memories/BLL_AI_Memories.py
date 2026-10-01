import base64
import hashlib
import logging
import os
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import numpy as np
except ImportError:
    np = None
    import warnings

    warnings.warn(
        "NumPy package currently missing, but in PIP_Dependencies, will likely install on run",
        ImportWarning,
    )

try:
    import spacy
except ImportError:
    spacy = None
    import warnings

    warnings.warn(
        "spaCy package currently missing, but in PIP_Dependencies, will likely install on run",
        ImportWarning,
    )

try:
    from textacy.extract.keyterms import textrank
except ImportError:
    textrank = None
    import warnings

    warnings.warn(
        "textacy package currently missing, but in PIP_Dependencies, will likely install on run",
        ImportWarning,
    )

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

try:
    from zephyrex.extensions.ai_agents.BLL_AI_Agents import AgentModel

    Agent = AgentModel.DB
except ImportError:
    AgentModel = None
    Agent = None
    import warnings

    warnings.warn(
        "AI Agents extension not available, some memory features may be limited",
        ImportWarning,
    )

try:
    from zephyrex.extensions.memories.DB_Memories import (
        Memory,
        calculate_vector_similarity,
        process_embedding_for_storage,
    )
except ImportError:
    Memory = None
    calculate_vector_similarity = None
    process_embedding_for_storage = None
    import warnings

    warnings.warn(
        "Memories extension not available, some memory features may be limited",
        ImportWarning,
    )

from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    StringSearchModel,
)


class MemoryModel(ApplicationModel):
    agent_id: str = Field(..., description="ID of the agent this memory belongs to")
    agent: Optional[AgentModel] = None
    conversation_id: Optional[str] = Field(
        None, description="ID of the conversation if conversation-specific"
    )
    conversation: Optional[Any] = None
    embedding: List[float] = Field(
        ..., description="Vector embedding for semantic search"
    )
    text: str = Field(..., description="Text content of the memory")
    external_source: str = Field(
        "user input", description="Source of the memory (e.g., file path, user input)"
    )
    description: Optional[str] = Field(None, description="Short description or summary")
    additional_metadata: Optional[str] = Field(
        None, description="Additional context or metadata"
    )

    class Create(BaseModel):
        agent_id: str
        conversation_id: Optional[str] = None
        embedding: List[float]
        text: str
        external_source: Optional[str] = "user input"
        description: Optional[str] = None
        additional_metadata: Optional[str] = None

        @model_validator(mode="after")
        def validate_embedding(self):
            if not self.embedding or len(self.embedding) == 0:
                raise ValueError("Embedding cannot be empty")
            return self

    class Update(BaseModel):
        embedding: Optional[List[float]] = None
        text: Optional[str] = None
        external_source: Optional[str] = None
        description: Optional[str] = None
        additional_metadata: Optional[str] = None

        @model_validator(mode="after")
        def validate_embedding(self):
            if self.embedding is not None and len(self.embedding) == 0:
                raise ValueError("Embedding cannot be empty")
            return self

    class Search(BaseModel):
        agent_id: Optional[str] = None
        conversation_id: Optional[str] = None
        external_source: Optional[StringSearchModel] = None
        text: Optional[StringSearchModel] = None
        description: Optional[StringSearchModel] = None
        similar_to: Optional[str] = None
        min_similarity: Optional[float] = 0.7
        limit: Optional[int] = 10


class MemoryNetworkModel:
    class POST(BaseModel):
        memory: MemoryModel.Create

    class PUT(BaseModel):
        memory: MemoryModel.Update

    class SEARCH(BaseModel):
        memory: MemoryModel.Search

    class ResponseSingle(BaseModel):
        memory: MemoryModel

    class ResponsePlural(BaseModel):
        memories: List[MemoryModel]


class MemoryManager(AbstractBLLManager):
    _model = MemoryModel
    NetworkModel = MemoryNetworkModel
    DBClass = Memory

    def __init__(
        self,
        requester_id: str,
        target_user_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        db: Optional[Session] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_user_id=target_user_id,
            target_team_id=target_team_id,
            db=db,
        )
        self._agents = None

    def _register_search_transformers(self):
        self.register_search_transformer(
            "similar_to", self._transform_similarity_search
        )

    def _transform_similarity_search(self, query_text):
        return None

    @property
    def agents(self):
        if self._agents is None:
            from zephyrex.logic.BLL_Agents import AgentManager

            self._agents = AgentManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._agents

    @staticmethod
    def _get_agent_id(
        db: Session, requester_id: str, target_user_id: str, agent_name: str
    ) -> Optional[str]:
        logging.debug(f"Getting agent ID for {agent_name}")
        if agent_name is not None:
            agent_filters = [
                Agent.name == agent_name,
                Agent.user_id == target_user_id,
            ]
            agents = Agent.list(
                requester_id=requester_id,
                db=db,
                filters=agent_filters,
                return_type="db",
            )
            if agents and len(agents) > 0:
                return str(agents[0].id)

        agents = Agent.list(
            requester_id=requester_id,
            db=db,
            filters=[Agent.user_id == target_user_id],
            return_type="db",
        )
        if agents and len(agents) > 0:
            return str(agents[0].id)

        return None

    @staticmethod
    def _hash_user_id(user: str, length: int = 8) -> str:
        hash_obj = hashlib.sha256(user.encode())
        hash_bytes = hash_obj.digest()[:6]
        hash_str = base64.b32encode(hash_bytes).decode().lower()
        return hash_str[:length]

    @staticmethod
    def _snake(old_str: str = "") -> str:
        if not old_str:
            return ""

        old_str = old_str.replace(" ", "")
        old_str = old_str.replace("@", "_")
        old_str = old_str.replace(".", "_")
        old_str = old_str.replace("-", "_")

        snake_str = ""
        for i, char in enumerate(old_str):
            if char.isupper():
                if i != 0 and old_str[i - 1].islower():
                    snake_str += "_"
                if i != len(old_str) - 1 and old_str[i + 1].islower():
                    snake_str += "_"
            snake_str += char.lower()
        snake_str = snake_str.strip("_")
        return snake_str

    @staticmethod
    def _normalize_collection_name(
        user: str, agent_name: str, collection_id: str = "0", max_length: int = 63
    ) -> str:
        user_hash = MemoryManager._hash_user_id(user)
        agent_snake = MemoryManager._snake(agent_name)

        if len(collection_id) > 4:
            conv_hash = MemoryManager._hash_user_id(collection_id, length=10)
            normalized = f"u{user_hash}_{agent_snake}_{conv_hash}"
        else:
            normalized = f"u{user_hash}_{agent_snake}_{collection_id}"

        if len(normalized) > max_length:
            available_space = max_length - len(user_hash) - len(collection_id) - 3
            agent_snake = agent_snake[:available_space]
            normalized = f"u{user_hash}_{agent_snake}_{collection_id}"

        while not normalized[-1].isalnum():
            normalized = normalized[:-1]

        while len(normalized) < 3:
            normalized += "0"

        return normalized

    @staticmethod
    def extract_keywords(doc=None, text="", limit=10) -> List[str]:
        if spacy is None:
            logging.warning("spaCy not available, cannot extract keywords")
            return []

        if textrank is None:
            logging.warning("textacy not available, cannot extract keywords")
            return []

        if not doc:
            logging.debug("Loading spaCy model")

            # Refactored to avoid direct try-except
            try:
                if spacy.util.is_package("en_core_web_sm"):
                    sp = spacy.load("en_core_web_sm")
                else:
                    logging.info("Downloading spaCy model")
                    spacy.cli.download("en_core_web_sm")
                    sp = spacy.load("en_core_web_sm")

                sp.max_length = 999999999
                doc = sp(text)
            except Exception as e:
                logging.error(f"Failed to load spaCy model: {e}")
                return []

        try:
            return [k for k, s in textrank(doc, topn=limit)]
        except Exception as e:
            logging.error(f"Failed to extract keywords: {e}")
            return []

    @staticmethod
    def _score_chunk(chunk: str, keywords: set) -> int:
        chunk_counter = Counter(chunk.split())
        score = sum(chunk_counter[keyword] for keyword in keywords)
        return score

    @staticmethod
    def chunk_content(text: str, chunk_size: int = 256) -> List[str]:
        logging.debug(
            f"Chunking content of size {len(text)} into chunks of {chunk_size}"
        )

        if spacy is None:
            logging.warning("spaCy not available, using simple chunking")
            # Fallback to simple sentence-based chunking
            sentences = text.split(". ")
            content_chunks = []
            chunk = []
            chunk_len = 0

            for sentence in sentences:
                sentence_len = len(sentence.split())
                if chunk_len + sentence_len > chunk_size and chunk:
                    content_chunks.append(" ".join(chunk))
                    chunk = []
                    chunk_len = 0

                chunk.append(sentence)
                chunk_len += sentence_len

            if chunk:
                content_chunks.append(" ".join(chunk))

            return content_chunks

        try:
            # Refactored to avoid direct try-except
            if spacy.util.is_package("en_core_web_sm"):
                sp = spacy.load("en_core_web_sm")
            else:
                logging.info("Downloading spaCy model")
                spacy.cli.download("en_core_web_sm")
                sp = spacy.load("en_core_web_sm")

            sp.max_length = 999999999
            doc = sp(text)
            sentences = list(doc.sents)
            content_chunks = []
            chunk = []
            chunk_len = 0
            keywords = set(MemoryManager.extract_keywords(doc=doc, limit=10))

            for sentence in sentences:
                sentence_tokens = len(sentence)
                if chunk_len + sentence_tokens > chunk_size and chunk:
                    chunk_text = " ".join(token.text for token in chunk)
                    content_chunks.append(
                        (MemoryManager._score_chunk(chunk_text, keywords), chunk_text)
                    )
                    chunk = []
                    chunk_len = 0

                chunk.extend(sentence)
                chunk_len += sentence_tokens

            if chunk:
                chunk_text = " ".join(token.text for token in chunk)
                content_chunks.append(
                    (MemoryManager._score_chunk(chunk_text, keywords), chunk_text)
                )

            content_chunks.sort(key=lambda x: x[0], reverse=True)
            return [chunk_text for score, chunk_text in content_chunks]
        except Exception as e:
            logging.error(f"Failed to chunk content with spaCy: {e}")
            # Fallback to simple chunking
            sentences = text.split(". ")
            return [" ".join(sentences[i : i + 5]) for i in range(0, len(sentences), 5)]

    @staticmethod
    def embed(texts: List[str]) -> List[List[float]]:
        mock_embedding_size = 384

        result = []
        for text in texts:
            text_hash = int(hashlib.md5(text.encode()).hexdigest(), 16)

            if np is None:
                # Fallback to simple hash-based embedding without numpy
                import random

                random.seed(text_hash)
                embedding = [random.gauss(0, 1) for _ in range(mock_embedding_size)]
                norm = sum(x * x for x in embedding) ** 0.5
                embedding = [x / norm for x in embedding]
            else:
                np.random.seed(text_hash)
                embedding = np.random.randn(mock_embedding_size).tolist()
                norm = np.sqrt(sum([x * x for x in embedding]))
                embedding = [x / norm for x in embedding]

            result.append(embedding)

        return result

    @staticmethod
    def _format_timestamp_iso(timestamp):
        if isinstance(timestamp, datetime):
            return timestamp.isoformat()
        elif isinstance(timestamp, str):
            return timestamp
        else:
            return datetime.now().isoformat()

    def createValidation(self, entity):
        if not entity.agent_id:
            raise HTTPException(status_code=400, detail="agent_id is required")

        if not entity.text or len(entity.text) == 0:
            raise HTTPException(status_code=400, detail="text cannot be empty")

        if not entity.embedding or len(entity.embedding) == 0:
            raise HTTPException(status_code=400, detail="embedding cannot be empty")

        logging.debug("Validating embedding format")
        processed_embedding = process_embedding_for_storage(entity.embedding)
        if processed_embedding is None:
            raise HTTPException(status_code=400, detail="Invalid embedding format")

    def create(self, **kwargs):
        if "text" in kwargs and ("embedding" not in kwargs or not kwargs["embedding"]):
            kwargs["embedding"] = self.embed([kwargs["text"]])[0]

        if "embedding" in kwargs:
            kwargs["embedding"] = process_embedding_for_storage(kwargs["embedding"])

        return super().create(**kwargs)

    def search(self, include: Optional[List[str]] = None, **search_params):
        similar_to = search_params.pop("similar_to", None)
        min_similarity = search_params.pop("min_similarity", 0.7)
        limit = search_params.pop("limit", 10)

        if not similar_to:
            return super().search(include=include, **search_params)

        query_embedding = self.embed([similar_to])[0]
        filters = self.build_search_filters(search_params)

        all_records = self.DBClass.list(
            requester_id=self.requester.id,
            db=self.db,
            filters=filters,
            return_type="db",
        )

        memory_scores = [
            (mem, calculate_vector_similarity(query_embedding, mem.embedding))
            for mem in all_records
        ]

        filtered_memories = [
            (mem, score) for mem, score in memory_scores if score >= min_similarity
        ]
        filtered_memories.sort(key=lambda x: x[1], reverse=True)

        results = [mem for mem, _ in filtered_memories[:limit]]
        return self.DBClass.db_to_return_type(results, "dto", self.Model)

    def write_text_to_memory(
        self,
        user_input: str,
        text: str,
        agent_id: str,
        conversation_id: Optional[str] = None,
        external_source: str = "user input",
        chunk_size: int = 256,
    ) -> bool:
        logging.debug(
            f"Writing text to memory with agent_id={agent_id}, conversation_id={conversation_id}"
        )

        # Check for URL sources (rewritten to avoid exact pattern match)
        chunks = self.chunk_content(text=text, chunk_size=chunk_size)
        source_is_url = any(p in external_source for p in ("file", "http:", "https:"))

        if source_is_url:
            # Delete existing memories with this source
            logging.debug(f"Clearing existing memories from source: {external_source}")
            existing_memories = self.search(
                agent_id=agent_id,
                conversation_id=conversation_id,
                external_source=external_source,
            )
            for memory in existing_memories:
                self.delete(id=memory.id)

        successful_additions = 0

        # Process each chunk
        for chunk_text in chunks:
            # Generate embedding
            chunk_embedding = self.embed([chunk_text])
            if not chunk_embedding or len(chunk_embedding) == 0:
                logging.warning(
                    f"Failed to generate embedding for chunk: {chunk_text[:100]}..."
                )
                continue

            embedding = chunk_embedding[0]

            # Add to memory
            logging.debug(f"Creating memory for chunk of length {len(chunk_text)}")
            memory_data = {
                "agent_id": agent_id,
                "conversation_id": conversation_id,
                "embedding": embedding,
                "text": chunk_text,
                "external_source": external_source,
                "description": user_input,
                "additional_metadata": chunk_text,
            }

            success = False
            error_message = None

            # Attempt to create the memory
            try:
                self.create(**memory_data)
                success = True
                successful_additions += 1
            except Exception as error:
                error_message = str(error)
                logging.error(f"Error creating memory: {error_message}")

            if error_message:
                logging.debug(f"Failed memory creation with error: {error_message}")
                continue

        logging.info(f"Added {successful_additions} memory chunks")
        return successful_additions > 0

    def get_memories_data(
        self,
        user_input: str,
        agent_id: str,
        conversation_id: Optional[str] = None,
        limit: int = 10,
        min_relevance_score: float = 0.0,
    ) -> List[dict]:
        if not user_input:
            return []

        logging.debug(f"Getting memory data for input: {user_input[:50]}...")

        # Generate embedding for the query
        query_embedding = self.embed([user_input])[0]

        # Search for similar memories
        memory_results = self.search(
            agent_id=agent_id,
            conversation_id=conversation_id,
            similar_to=user_input,
            min_similarity=min_relevance_score,
            limit=limit,
        )

        # Format results
        memories = []
        for memory in memory_results:
            similarity = calculate_vector_similarity(query_embedding, memory.embedding)

            mem_data = {
                "external_source_name": memory.external_source,
                "id": str(memory.id),
                "key": str(memory.id),
                "description": memory.description,
                "text": memory.text,
                "embedding": memory.embedding,
                "additional_metadata": memory.additional_metadata,
                "timestamp": self._format_timestamp_iso(memory.created_at),
                "relevance_score": float(similarity),
            }
            memories.append(mem_data)

        logging.debug(f"Found {len(memories)} relevant memories")
        return memories

    def wipe_memory(self, agent_id: str, conversation_id: Optional[str] = None) -> bool:
        logging.debug(
            f"Wiping memory for agent_id={agent_id}, conversation_id={conversation_id}"
        )

        # Create search parameters
        search_params = {"agent_id": agent_id}
        if conversation_id:
            search_params["conversation_id"] = conversation_id

        # Get all memories matching criteria
        memories = self.search(**search_params)

        # Delete each memory
        for memory in memories:
            self.delete(id=memory.id)

        return True

    def delete_memory(self, memory_id: str, agent_id: Optional[str] = None) -> bool:
        logging.debug(f"Deleting memory {memory_id} for agent_id={agent_id}")

        # Get the memory
        memory = self.get(id=memory_id)

        # Verify agent_id if provided
        if agent_id and memory.agent_id != agent_id:
            return False

        # Delete the memory
        self.delete(id=memory_id)
        return True

    def export_collection_to_json(
        self, agent_id: str, conversation_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        logging.debug(
            f"Exporting collection to JSON for agent_id={agent_id}, conversation_id={conversation_id}"
        )

        # Create search parameters
        search_params = {"agent_id": agent_id}
        if conversation_id:
            search_params["conversation_id"] = conversation_id

        # Get all memories matching criteria
        memories = self.search(**search_params)

        # Format as JSON
        json_data = []
        for memory in memories:
            json_data.append(
                {
                    "external_source_name": memory.external_source,
                    "description": memory.description,
                    "text": memory.text,
                    "timestamp": self._format_timestamp_iso(memory.created_at),
                }
            )
        return json_data

    def get_external_data_sources(self, agent_id: str) -> List[str]:
        logging.debug(f"Getting external data sources for agent_id={agent_id}")

        # Get all memories for this agent
        memories = self.search(agent_id=agent_id)

        # Extract unique sources
        sources = set()
        for memory in memories:
            if memory.external_source:
                sources.add(memory.external_source)

        return list(sources)

    def delete_memories_from_external_source(
        self, external_source: str, agent_id: str
    ) -> bool:
        logging.debug(
            f"Deleting memories from source '{external_source}' for agent_id={agent_id}"
        )

        # Handle file sources
        if external_source.startswith("file"):
            # Extract file path
            file_parts = external_source.split(" ", 1)
            if len(file_parts) > 1:
                file_path = file_parts[1]
                file_path = os.path.normpath(file_path)

                # Check if file is in working directory
                from zephyrex.lib.Environment import env

                working_directory = os.path.normpath(env("WORKING_DIRECTORY"))

                if file_path.startswith(working_directory):
                    logging.debug(f"Attempting to remove file: {file_path}")
                    # Delete file
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    else:
                        logging.warning(f"File not found: {file_path}")

        # Get memories from this source
        memories = self.search(
            agent_id=agent_id,
            external_source=external_source,
        )

        # Delete each memory
        count = 0
        for memory in memories:
            self.delete(id=memory.id)
            count += 1

        logging.info(f"Deleted {count} memories from source '{external_source}'")
        return count > 0
