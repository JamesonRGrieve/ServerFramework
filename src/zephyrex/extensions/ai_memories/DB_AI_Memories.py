import logging

import numpy as np
from sqlalchemy import Column, Text, event, text
from sqlalchemy.orm import relationship
from sqlalchemy.types import TypeDecorator

from zephyrex.database.AbstractDatabaseEntity import BaseMixin, UpdateMixin
from zephyrex.database.Base import DATABASE_TYPE, Base
from zephyrex.extensions.ai_agents.BLL_AI_Agents import AgentModel
from zephyrex.extensions.conversations.BLL_Conversations import ConversationModel


class Vector(TypeDecorator):
    """Unified vector storage for both SQLite and PostgreSQL"""

    impl = Text

    def process_bind_param(self, value, dialect):
        """Convert vector to storage format"""
        if value is None:
            return None
        # Convert to numpy array and ensure 1D
        if isinstance(value, np.ndarray):
            value = value.reshape(-1).tolist()
        elif isinstance(value, list):
            # Handle nested lists
            value = np.array(value).reshape(-1).tolist()
        if DATABASE_TYPE == "sqlite":
            return f'[{",".join(map(str, value))}]'
        # For PostgreSQL, return as list
        return value

    def process_result_value(self, value, dialect):
        """Convert from storage format to numpy array"""
        if value is None:
            return None
        # For SQLite, parse string representation
        if DATABASE_TYPE == "sqlite":
            try:
                value = eval(value)
            except:
                return None
        # Convert to 1D numpy array
        return np.array(value).reshape(-1)


# Update the embedding function to ensure consistent output shape
def process_embedding_for_storage(embedding):
    if embedding is None:
        return None
    # Convert to numpy array and ensure 1D
    if isinstance(embedding, list) or isinstance(embedding, np.ndarray):
        return np.array(embedding).reshape(-1)
    return None


class Memory(
    Base, BaseMixin, UpdateMixin, AgentRefMixin, ConversationRefMixin.Optional
):
    __tablename__ = "memories"
    embedding = Column(Vector)
    text = Column(Text, nullable=False)
    external_source = Column(Text, nullable=False, default="user input")
    description = Column(Text)
    additional_metadata = Column(Text)
    conversation = relationship(Conversation.__name__, backref="memories")


@event.listens_for(Memory.__table__, "after_create")
def setup_vector_column(target, connection, **kw):
    try:
        # Create basic indices that will be useful for both databases
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS memory_agent_conv_idx ON memories (agent_id, conversation_id)"
            )
        )
    except Exception as e:
        logging.error(f"Error setting up memory indices: {e}")


def calculate_vector_similarity(query_embedding, stored_embedding):
    """Calculate cosine similarity between two vectors"""
    if query_embedding is None or stored_embedding is None:
        return 0.0
    try:
        if not isinstance(query_embedding, np.ndarray):
            query_embedding = np.array(query_embedding)
        if not isinstance(stored_embedding, np.ndarray):
            stored_embedding = np.array(stored_embedding)

        # Ensure vectors are 1D and have the same shape
        query_embedding = query_embedding.reshape(-1)  # Flatten to 1D
        stored_embedding = stored_embedding.reshape(-1)  # Flatten to 1D

        # Verify shapes match
        if query_embedding.shape != stored_embedding.shape:
            logging.warning(
                f"Vector shape mismatch: {query_embedding.shape} vs {stored_embedding.shape}"
            )
            return 0.0

        # Calculate cosine similarity
        dot_product = np.dot(query_embedding, stored_embedding)
        query_norm = np.linalg.norm(query_embedding)
        stored_norm = np.linalg.norm(stored_embedding)

        if query_norm == 0 or stored_norm == 0:
            return 0.0
        return float(dot_product / (query_norm * stored_norm))
    except Exception as e:
        logging.error(f"Error calculating vector similarity: {e}")
        return 0.0


# Update the memory search query for both databases:
def get_similar_memories(
    session, query_embedding, agent_id, conversation_id, limit, min_score
):
    try:
        # Get all potentially relevant memories
        memories = (
            session.query(Memory)
            .filter(
                Memory.agent_id == agent_id, Memory.conversation_id == conversation_id
            )
            .all()
        )

        # Calculate similarities
        memory_scores = [
            (mem, calculate_vector_similarity(query_embedding, mem.embedding))
            for mem in memories
        ]

        filtered_memories = [
            (mem, score) for mem, score in memory_scores if score >= min_score
        ]

        filtered_memories.sort(key=lambda x: x[1], reverse=True)

        # Return top N results
        return [mem for mem, _ in filtered_memories[:limit]]
    except Exception as e:
        logging.error(f"Error in memory search: {e}")
        return []
