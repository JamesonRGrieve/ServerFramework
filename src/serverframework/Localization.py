import inspect
import json
import os
from typing import Any, Dict

import stringcase

from serverframework.lib.Environment import inflection
from serverframework.lib.Logging import logger

# Examples:

# # Example: Using localization in database models with relationships

# from sqlalchemy import Column, Text, Boolean, Integer
# from sqlalchemy.orm import relationship as sqlalchemy_relationship
# import inspect

# from database.DatabaseManager import db_manager
# from database.Mixins import BaseMixin, UpdateMixin
# from Localization import localized_model, localization, relationship, foreign_key


# # Method 1: Using the decorator with auto-generated tablename
# @localized_model
# class Project(Base, BaseMixin, UpdateMixin):
#     # No __tablename__ specified - it will be automatically generated
#     # from the plural form in the localization file
#     name = Column(Text, nullable=False)
#     description = Column(Text, nullable=True)

#     # Using localized relationship helper - no need to specify back_populates
#     conversations = relationship("Conversation")

#     # You can still specify back_populates if needed
#     artifacts = relationship("Artifact", back_populates="project")


# # Method 2: Applying localization directly
# @localized_model
# class Conversation(Base, BaseMixin, UpdateMixin):
#     name = Column(Text, nullable=False)
#     description = Column(Text, nullable=True)

#     # Using localized foreign key helper - automatically targets the correct table
#     project_id = foreign_key("Project")

#     # Using localized relationship helper - back_populates is handled automatically
#     project = relationship("Project")

#     # Relationship to messages - back_populates automatically set to "conversations"
#     messages = relationship("Message")


# # Example with more complex relationships
# @localized_model
# class Message(Base, BaseMixin):
#     role = Column(Text, nullable=False)
#     content = Column(Text, nullable=False)

#     # Foreign key to conversation - non-nullable
#     conversation_id = foreign_key("Conversation", nullable=False)
#     conversation = relationship("Conversation")

#     # Relationships to activities and artifacts
#     activities = relationship("MessageActivity")
#     artifacts = relationship("MessageArtifact")
#     source_of_artifacts = relationship("Artifact")


# # Example with many-to-many relationship
# @localized_model
# class MessageActivity(Base, BaseMixin):
#     title = Column(Text, nullable=False)
#     body = Column(Text, nullable=False)

#     # Foreign key to message
#     message_id = foreign_key("Message")
#     message = relationship("Message")

#     # Foreign key to chain step
#     chain_link_id = foreign_key("ChainLink")
#     chain_link = relationship("ChainLink")


# @localized_model
# class Artifact(Base, BaseMixin):
#     name = Column(Text, nullable=False)
#     relative_path = Column(Text, nullable=False)
#     hosted_path = Column(Text, nullable=False)
#     encrypted = Column(Boolean, nullable=False, default=False)

#     # Foreign keys with custom relationship names
#     project_id = foreign_key("Project")
#     project = relationship("Project")

#     source_message_id = foreign_key("Message")
#     # Custom back_populates name
#     source_message = relationship("Message", back_populates="source_of_artifacts")

#     # Many-to-many relationship through MessageArtifact
#     messages = relationship("MessageArtifact")


# @localized_model
# class MessageArtifact(Base, BaseMixin):
#     # Junction table for many-to-many relationship
#     message_id = foreign_key("Message")
#     message = relationship("Message")

#     artifact_id = foreign_key("Artifact")
#     artifact = relationship("Artifact")


# # Example with custom plural and automatic tablename
# @localized_model
# class CustomEntityWithLongName(Base, BaseMixin):
#     # No __tablename__ specified - will be generated from plural form
#     # If plural in localization is "Custom Entities With Long Names",
#     # the tablename will be "custom_entities_with_long_names"
#     name = Column(Text, nullable=False)
#     is_active = Column(Boolean, default=True)


# # Example demonstrating foreign key with custom column name
# @localized_model
# class Employee(Base, BaseMixin):
#     name = Column(Text, nullable=False)

#     # Custom foreign key column name
#     supervisor_id = foreign_key("Employee", name="supervisor_id", nullable=True)
#     supervisor = relationship("Employee", remote_side="Employee.id", backref="subordinates")

#     # Regular foreign key
#     project_id = foreign_key("Project", nullable=True)
#     project = relationship("Project")


# # Using localization utility functions
# def display_entity_info(domain, entity):
#     """Display comprehensive information about an entity"""
#     logger.debug(f"Entity: {entity} (Domain: {domain})")

#     # Get metadata
#     singular = localization.get_entity_singular(domain, entity)
#     plural = localization.get_entity_plural(domain, entity)
#     comment = localization.get_entity_comment(domain, entity)

#     logger.debug(f"Singular: {singular}")
#     logger.debug(f"Plural: {plural}")
#     logger.debug(f"Description: {comment}")

#     # Get model class to extract properties
#     module_name = f"serverframework.database.DB_{domain.capitalize()}"
#     try:
#         module = __import__(module_name, fromlist=[entity])
#         cls = getattr(module, entity)

#         # Get tablename (auto-generated or manually defined)
#         logger.debug(f"Tablename: {cls.__tablename__}")

#         logger.debug("\nProperties:")
#         for name, attr in inspect.getmembers(cls):
#             if isinstance(attr, Column) and not name.startswith('_'):
#                 prop_comment = localization.get_property_comment(domain, entity, name)
#                 logger.debug(f"  - {name}: {prop_comment}")

#         logger.debug("\nRelationships:")
#         if hasattr(cls, "__mapper__") and hasattr(cls.__mapper__, "relationships"):
#             for name, rel in cls.__mapper__.relationships.items():
#                 target_cls = rel.argument() if callable(rel.argument) else rel.argument
#                 target_name = target_cls.__name__ if hasattr(target_cls, "__name__") else str(target_cls)
#                 logger.debug(f"  - {name}: Relates to {target_name} via {rel.back_populates or 'N/A'}")
#     except (ImportError, AttributeError) as e:
#         logger.debug(f"Could not inspect class: {e}")


# # Example API function with localized documentation
# def create_api_endpoint(domain, entity):
#     """Generate API endpoint documentation for CRUD operations"""

#     singular = localization.get_entity_singular(domain, entity)
#     plural = localization.get_entity_plural(domain, entity)

#     endpoints = {
#         "list": {
#             "summary": f"List all {plural}",
#             "description": f"Returns a paginated list of all {plural}"
#         },
#         "create": {
#             "summary": f"Create a new {singular}",
#             "description": f"Creates a new {singular} with the provided data"
#         },
#         "get": {
#             "summary": f"Get a {singular}",
#             "description": f"Returns details for a specific {singular}"
#         },
#         "update": {
#             "summary": f"Update a {singular}",
#             "description": f"Updates an existing {singular} with the provided data"
#         },
#         "delete": {
#             "summary": f"Delete a {singular}",
#             "description": f"Permanently removes a {singular}"
#         }
#     }

#     return endpoints


# # Example application
# if __name__ == "__main__":
#     # Print generated tablenames
#     logger.debug(f"Project tablename: {Project.__tablename__}")
#     logger.debug(f"Conversation tablename: {Conversation.__tablename__}")
#     logger.debug(f"Message tablename: {Message.__tablename__}")
#     logger.debug(f"CustomEntityWithLongName tablename: {CustomEntityWithLongName.__tablename__}")

#     # Display relationship information
#     logger.debug("\nProject relationships:")
#     for name, rel in Project.__mapper__.relationships.items():
#         target_cls = rel.argument() if callable(rel.argument) else rel.argument
#         target_name = target_cls.__name__ if hasattr(target_cls, "__name__") else str(target_cls)
#         logger.debug(f"  - {name}: Relates to {target_name} via {rel.back_populates or 'N/A'}")

#     # Display detailed entity info
#     logger.debug("\nDetailed entity info:")
#     display_entity_info("conversations", "Project")

#     # Generate API endpoint documentation
#     api_docs = create_api_endpoint("conversations", "Project")
#     logger.debug("\nAPI Endpoints:")
#     for endpoint, docs in api_docs.items():
#         logger.debug(f"  - {endpoint}: {docs['summary']}")
#         logger.debug(f"    {docs['description']}")
#         logger.debug()# Example: Using localization in database models with relationships

# from sqlalchemy import Column, Text, Boolean, ForeignKey, Integer
# from sqlalchemy.orm import relationship
# import inspect

# from database.DatabaseManager import db_manager
# from database.Mixins import BaseMixin, UpdateMixin, create_foreign_key
# from Localization import localized_model, localization


# # Method 1: Using the decorator with auto-generated tablename
# @localized_model
# class Project(Base, BaseMixin, UpdateMixin):
#     # No __tablename__ specified - it will be automatically generated
#     # from the plural form in the localization file
#     name = Column(Text, nullable=False)
#     description = Column(Text, nullable=True)

#     # Relationship that will be automatically back-populated
#     # The relationship name matches the class name of the target
#     conversations = relationship("Conversation", back_populates="project")


# # Method 2: Applying localization directly
# @localized_model
# class Conversation(Base, BaseMixin, UpdateMixin):
#     name = Column(Text, nullable=False)
#     description = Column(Text, nullable=True)

#     # Foreign key using create_foreign_key utility that will match the table name
#     # of the target class which is auto-generated from localization
#     project_id = create_foreign_key("Project")

#     # Define the relationship with back_populates
#     project = relationship("Project", back_populates="conversations")

#     # Relationship to messages
#     messages = relationship("Message", back_populates="conversation")


# # Example with more complex relationships
# @localized_model
# class Message(Base, BaseMixin):
#     role = Column(Text, nullable=False)
#     content = Column(Text, nullable=False)

#     # Foreign key to conversation
#     conversation_id = create_foreign_key("Conversation", nullable=False)
#     conversation = relationship("Conversation", back_populates="messages")

#     # Relationships to activities and artifacts
#     activities = relationship("MessageActivity", back_populates="message")
#     artifacts = relationship("MessageArtifact", back_populates="message")
#     source_of_artifacts = relationship("Artifact", back_populates="source_message")


# # Example with many-to-many relationship
# @localized_model
# class MessageActivity(Base, BaseMixin):
#     title = Column(Text, nullable=False)
#     body = Column(Text, nullable=False)

#     # Foreign key to message
#     message_id = create_foreign_key("Message")
#     message = relationship("Message", back_populates="activities")

#     # Foreign key to chain step
#     chain_link_id = create_foreign_key("ChainLink")
#     chain_link = relationship("ChainLink", back_populates="message_activities")


# @localized_model
# class Artifact(Base, BaseMixin):
#     name = Column(Text, nullable=False)
#     relative_path = Column(Text, nullable=False)
#     hosted_path = Column(Text, nullable=False)
#     encrypted = Column(Boolean, nullable=False, default=False)

#     # Foreign keys
#     project_id = create_foreign_key("Project")
#     project = relationship("Project", back_populates="artifacts")

#     source_message_id = create_foreign_key("Message")
#     source_message = relationship("Message", back_populates="source_of_artifacts")

#     # Many-to-many relationship through MessageArtifact
#     messages = relationship("MessageArtifact", back_populates="artifact")


# @localized_model
# class MessageArtifact(Base, BaseMixin):
#     # Junction table for many-to-many relationship
#     message_id = create_foreign_key("Message")
#     message = relationship("Message", back_populates="artifacts")

#     artifact_id = create_foreign_key("Artifact")
#     artifact = relationship("Artifact", back_populates="messages")


# # Example with automatic tablename generation
# @localized_model
# class CustomEntityWithLongName(Base, BaseMixin):
#     # No __tablename__ specified - will be generated from plural form
#     # If plural in localization is "Custom Entities With Long Names",
#     # the tablename will be "custom_entities_with_long_names"
#     name = Column(Text, nullable=False)
#     is_active = Column(Boolean, default=True)


# # Using localization utility functions
# def display_entity_info(domain, entity):
#     """Display comprehensive information about an entity"""
#     logger.debug(f"Entity: {entity} (Domain: {domain})")

#     # Get metadata
#     singular = localization.get_entity_singular(domain, entity)
#     plural = localization.get_entity_plural(domain, entity)
#     comment = localization.get_entity_comment(domain, entity)

#     logger.debug(f"Singular: {singular}")
#     logger.debug(f"Plural: {plural}")
#     logger.debug(f"Description: {comment}")

#     # Get model class to extract properties
#     module_name = f"serverframework.database.DB_{domain.capitalize()}"
#     try:
#         module = __import__(module_name, fromlist=[entity])
#         cls = getattr(module, entity)

#         # Get tablename (auto-generated or manually defined)
#         logger.debug(f"Tablename: {cls.__tablename__}")

#         logger.debug("\nProperties:")
#         for name, attr in inspect.getmembers(cls):
#             if isinstance(attr, Column) and not name.startswith('_'):
#                 prop_comment = localization.get_property_comment(domain, entity, name)
#                 logger.debug(f"  - {name}: {prop_comment}")

#         logger.debug("\nRelationships:")
#         if hasattr(cls, "__mapper__") and hasattr(cls.__mapper__, "relationships"):
#             for name, rel in cls.__mapper__.relationships.items():
#                 target_cls = rel.argument() if callable(rel.argument) else rel.argument
#                 target_name = target_cls.__name__ if hasattr(target_cls, "__name__") else str(target_cls)
#                 logger.debug(f"  - {name}: Relates to {target_name} via {rel.back_populates or 'N/A'}")
#     except (ImportError, AttributeError) as e:
#         logger.debug(f"Could not inspect class: {e}")


# # Example API function with localized documentation
# def create_api_endpoint(domain, entity):
#     """Generate API endpoint documentation for CRUD operations"""

#     singular = localization.get_entity_singular(domain, entity)
#     plural = localization.get_entity_plural(domain, entity)

#     endpoints = {
#         "list": {
#             "summary": f"List all {plural}",
#             "description": f"Returns a paginated list of all {plural}"
#         },
#         "create": {
#             "summary": f"Create a new {singular}",
#             "description": f"Creates a new {singular} with the provided data"
#         },
#         "get": {
#             "summary": f"Get a {singular}",
#             "description": f"Returns details for a specific {singular}"
#         },
#         "update": {
#             "summary": f"Update a {singular}",
#             "description": f"Updates an existing {singular} with the provided data"
#         },
#         "delete": {
#             "summary": f"Delete a {singular}",
#             "description": f"Permanently removes a {singular}"
#         }
#     }

#     return endpoints


# # Example application
# if __name__ == "__main__":
#     # Print generated tablenames
#     logger.debug(f"Project tablename: {Project.__tablename__}")
#     logger.debug(f"Conversation tablename: {Conversation.__tablename__}")
#     logger.debug(f"Message tablename: {Message.__tablename__}")
#     logger.debug(f"CustomEntityWithLongName tablename: {CustomEntityWithLongName.__tablename__}")

#     # Display relationship information
#     logger.debug("\nProject relationships:")
#     for name, rel in Project.__mapper__.relationships.items():
#         target_cls = rel.argument() if callable(rel.argument) else rel.argument
#         target_name = target_cls.__name__ if hasattr(target_cls, "__name__") else str(target_cls)
#         logger.debug(f"  - {name}: Relates to {target_name} via {rel.back_populates or 'N/A'}")

#     # Display detailed entity info
#     logger.debug("\nDetailed entity info:")
#     display_entity_info("conversations", "Project")

#     # Generate API endpoint documentation
#     api_docs = create_api_endpoint("conversations", "Project")
#     logger.debug("\nAPI Endpoints:")
#     for endpoint, docs in api_docs.items():
#         logger.debug(f"  - {endpoint}: {docs['summary']}")
#         logger.debug(f"    {docs['description']}")
#         logger.debug()


class Localization:
    """
    Manages localization of documentation strings throughout the application.
    This class loads localization files and provides methods to access
    localized strings for database models, API endpoints, and other components.
    """

    _instance = None
    _locales = {}
    _current_locale = "en"

    def __new__(cls):
        """Singleton pattern to ensure only one instance exists."""
        if cls._instance is None:
            cls._instance = super(Localization, cls).__new__(cls)
            cls._instance._load_locales()
        return cls._instance

    def _load_locales(self):
        """Load all available locale files from the application root."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # Find all locale files (docs.*.json)
        for filename in os.listdir(base_dir):
            if filename.startswith("docs.") and filename.endswith(".json"):
                locale = filename.split(".")[1]
                try:
                    with open(
                        os.path.join(base_dir, filename), "r", encoding="utf-8"
                    ) as f:
                        self._locales[locale] = json.load(f)
                except Exception as e:
                    logger.error(f"Error loading locale file {filename}: {e}")

    def set_locale(self, locale: str):
        """Set the current locale for the application."""
        if locale in self._locales:
            self._current_locale = locale
            return True
        return False

    def get_available_locales(self):
        """Return a list of available locales."""
        return list(self._locales.keys())

    def get_entity_metadata(self, domain: str, entity: str) -> Dict[str, Any]:
        """
        Get metadata for an entity.

        Args:
            domain: Domain name (e.g., "conversations")
            entity: Entity name (e.g., "Project")

        Returns:
            Dictionary of metadata or empty dict if not found
        """
        if (
            self._current_locale not in self._locales
            or domain not in self._locales[self._current_locale]
            or entity not in self._locales[self._current_locale][domain]
            or "meta" not in self._locales[self._current_locale][domain][entity]
        ):
            return {}

        return self._locales[self._current_locale][domain][entity]["meta"]

    def get_entity_comment(self, domain: str, entity: str) -> str:
        """
        Get the comment for an entity.

        Args:
            domain: Domain name
            entity: Entity name

        Returns:
            Comment string or empty string if not found
        """
        meta = self.get_entity_metadata(domain, entity)
        return meta.get("comment", "")

    def get_entity_singular(self, domain: str, entity: str) -> str:
        """
        Get the singular name for an entity.

        Args:
            domain: Domain name
            entity: Entity name

        Returns:
            Singular name or entity name if not found
        """
        meta = self.get_entity_metadata(domain, entity)
        return meta.get("singular", entity)

    def get_relationship_backref_name(
        self, source_domain, source_entity, target_entity
    ):
        """
        Generate a standard name for the back-reference in a relationship.

        Args:
            source_domain: Domain of the source entity
            source_entity: Name of the source entity
            target_entity: Name of the target entity

        Returns:
            Standardized back-reference name (usually plural of source entity)
        """
        # Try to find the source entity in localization data
        if (
            self._current_locale in self._locales
            and source_domain in self._locales[self._current_locale]
            and source_entity in self._locales[self._current_locale][source_domain]
        ):

            # Get the plural form of the source entity and convert to snake_case
            source_plural = self.get_entity_plural(source_domain, source_entity)
            return stringcase.snakecase(source_plural)

        # Default: use inflect to create proper plural form
        return inflection.plural(source_entity.lower())

    def create_localized_foreign_key(self, target_entity, name=None, **kwargs):
        """
        Create a foreign key column that refers to the correctly named table.

        Args:
            target_entity: Name of the target entity class
            name: Override name for the column (default is target_entity_id)
            **kwargs: Additional arguments to pass to Column()

        Returns:
            SQLAlchemy Column with ForeignKey constraint
        """
        from sqlalchemy import Column, ForeignKey
        from sqlalchemy.types import String

        # Get module info to determine domain
        frame = inspect.currentframe().f_back
        module = inspect.getmodule(frame)
        target_domain = None

        if module:
            module_name = module.__name__
            if "DB_" in module_name:
                target_domain = module_name.split("DB_")[-1].lower()

        # Try to find the target entity in all domains if domain is unknown
        if not target_domain or target_entity not in self._locales.get(
            self._current_locale, {}
        ).get(target_domain, {}):
            for domain in self._locales.get(self._current_locale, {}):
                if target_entity in self._locales[self._current_locale][domain]:
                    target_domain = domain
                    break

        # Get the target table name
        table_name = self.get_tablename_from_entity(target_domain, target_entity)

        # Create column name if not provided
        if not name:
            name = f"{target_entity.lower()}_id"

        # Create and return the column
        return Column(name, String, ForeignKey(f"{table_name}.id"), **kwargs)

    def apply_relationship_naming(
        self,
        relationship_obj,
        source_entity,
        target_entity,
        source_domain=None,
        target_domain=None,
    ):
        """
        Apply standardized naming conventions to SQLAlchemy relationships.

        Args:
            relationship_obj: SQLAlchemy relationship object
            source_entity: Name of the source entity class
            target_entity: Name of the target entity class
            source_domain: Domain of the source entity (optional)
            target_domain: Domain of the target entity (optional)

        Returns:
            Updated relationship object
        """
        # Try to infer domains if not provided
        if not source_domain or not target_domain:
            frame = inspect.currentframe().f_back
            module = inspect.getmodule(frame)

            if module:
                module_name = module.__name__
                if "DB_" in module_name and not source_domain:
                    source_domain = module_name.split("DB_")[-1].lower()
                if "DB_" in module_name and not target_domain:
                    # Assume same domain if not specified
                    target_domain = module_name.split("DB_")[-1].lower()

        # Set appropriate back_populates
        if (
            not hasattr(relationship_obj, "back_populates")
            or not relationship_obj.back_populates
        ):
            backref_name = self.get_relationship_backref_name(
                source_domain, source_entity, target_entity
            )
            relationship_obj.back_populates = backref_name

        return relationship_obj

    def get_property_comment(self, domain: str, entity: str, property_name: str) -> str:
        """
        Get the comment for a property.

        Args:
            domain: Domain name
            entity: Entity name
            property_name: Property name

        Returns:
            Comment string or empty string if not found
        """
        if (
            self._current_locale not in self._locales
            or domain not in self._locales[self._current_locale]
            or entity not in self._locales[self._current_locale][domain]
            or "properties" not in self._locales[self._current_locale][domain][entity]
            or property_name
            not in self._locales[self._current_locale][domain][entity]["properties"]
        ):
            return ""

        return self._locales[self._current_locale][domain][entity]["properties"][
            property_name
        ].get("comment", "")

    def get_db_doc(self, class_name: str) -> str:
        """
        Get documentation for a database model class.

        Args:
            class_name: Name of the database model class

        Returns:
            Localized documentation string for the class
        """
        # Try to infer the domain from the filename
        frame = inspect.currentframe().f_back
        module = inspect.getmodule(frame)

        if module:
            module_name = module.__name__
            # Extract domain from module name (e.g., database.DB_Conversations -> conversations)
            if "DB_" in module_name:
                domain = module_name.split("DB_")[-1].lower()
                comment = self.get_entity_comment(domain, class_name)
                if comment:
                    return comment

        # Fallback: search through all domains
        for domain in self._locales.get(self._current_locale, {}):
            comment = self.get_entity_comment(domain, class_name)
            if comment:
                return comment

        return f"Documentation for {class_name}"

    def get_swagger_doc(self, endpoint: str) -> str:
        """
        Get Swagger documentation for an API endpoint.

        Args:
            endpoint: API endpoint path in dot notation

        Returns:
            Localized documentation string for the endpoint
        """
        # Format: "domain.entity.endpoint"
        parts = endpoint.split(".")

        if len(parts) >= 3:
            domain = parts[0]
            entity = parts[1]
            endpoint_name = ".".join(parts[2:])

            if (
                self._current_locale in self._locales
                and domain in self._locales[self._current_locale]
                and entity in self._locales[self._current_locale][domain]
                and "endpoints" in self._locales[self._current_locale][domain][entity]
                and endpoint_name
                in self._locales[self._current_locale][domain][entity]["endpoints"]
            ):
                return self._locales[self._current_locale][domain][entity]["endpoints"][
                    endpoint_name
                ]

        return f"API documentation for {endpoint}"

    def get_tablename_from_entity(self, domain, entity):
        """
        Generate a standardized table name from an entity name.

        Args:
            domain: Domain name
            entity: Entity name

        Returns:
            Table name as lowercase plural with spaces replaced by underscores
        """
        plural = self.get_entity_plural(domain, entity)
        # Convert to snake_case
        return stringcase.snakecase(plural)

    def apply_to_class(self, cls):
        """
        Apply documentation strings to a class by updating its __doc__ attribute,
        __tablename__, and column comments.

        Args:
            cls: The class to update
        """
        class_name = cls.__name__

        # Try to determine the domain from the module name
        module = inspect.getmodule(cls)
        domain = None

        if module:
            module_name = module.__name__
            if "DB_" in module_name:
                domain = module_name.split("DB_")[-1].lower()

        # If we couldn't determine the domain, search for the class in all domains
        if not domain:
            for potential_domain in self._locales.get(self._current_locale, {}):
                if (
                    potential_domain in self._locales[self._current_locale]
                    and class_name
                    in self._locales[self._current_locale][potential_domain]
                ):
                    domain = potential_domain
                    break

        if not domain:
            return cls

        # Get entity comment
        comment = self.get_entity_comment(domain, class_name)
        if not comment:
            return cls

        # Update class docstring
        cls.__doc__ = comment

        # Set __tablename__ to lowercase plural with underscores if not already set
        if not hasattr(cls, "__tablename__"):
            cls.__tablename__ = self.get_tablename_from_entity(domain, class_name)

        # Update __table_args__ comment if it exists
        if hasattr(cls, "__table_args__") and isinstance(cls.__table_args__, dict):
            cls.__table_args__["comment"] = comment
        elif hasattr(cls, "__table_args__") and isinstance(cls.__table_args__, tuple):
            # If it's a tuple, the last element should be a dict with options
            args_list = list(cls.__table_args__)
            if args_list and isinstance(args_list[-1], dict):
                args_list[-1]["comment"] = comment
                cls.__table_args__ = tuple(args_list)
            else:
                # Append a new dict with the comment
                args_list.append({"comment": comment})
                cls.__table_args__ = tuple(args_list)
        else:
            # Create new __table_args__ with comment
            cls.__table_args__ = {"comment": comment}

        # Apply property comments if the class has attributes
        if hasattr(cls, "__mapper__") and hasattr(cls.__mapper__, "columns"):
            for column_name, column in cls.__mapper__.columns.items():
                prop_comment = self.get_property_comment(
                    domain, class_name, column_name
                )
                if prop_comment:
                    column.comment = prop_comment

        return cls


# Create a singleton instance
localization = Localization()


# Convenience helper methods attached to the singleton
def relationship(*args, **kwargs):
    """Convenience wrapper for creating localized relationships"""
    return localization.create_localized_relationship(*args, **kwargs)


def foreign_key(*args, **kwargs):
    """Convenience wrapper for creating localized foreign keys"""
    return localization.create_localized_foreign_key(*args, **kwargs)


# Decorator for applying documentation to database models
def localized_model(cls):
    """
    Decorator to apply localized documentation strings to a database model class.

    Usage:
        @localized_model
        class MyModel(Base, BaseMixin):
            __tablename__ = "my_models"
            # ...
    """
    return localization.apply_to_class(cls)


# Function to generate dynamic docstrings from the database schema
def generate_schema_docs():
    """
    Generate documentation strings from the database schema.
    This function can be used to populate the initial localization files
    by extracting comments from SQLAlchemy models.

    Returns:
        Dict containing documentation organized by domain and entity
    """
    import importlib
    import inspect
    import pkgutil
    import re

    from serverframework.database.DatabaseManager import db_manager

    schema_docs = {}

    # Regular expression to find column definitions
    column_pattern = re.compile(r"(\w+)\s*=\s*Column\(.*\)")

    # Dynamically import all modules in the database package
    from serverframework import database

    for _, module_name, _ in pkgutil.iter_modules(database.__path__):
        if module_name.startswith("DB_"):
            try:
                module = importlib.import_module(f"serverframework.database.{module_name}")
                domain = module_name[3:].lower()  # Extract domain from DB_Domain

                # Initialize domain dictionary
                if domain not in schema_docs:
                    schema_docs[domain] = {}

                # Find all SQLAlchemy model classes in the module
                for name, obj in inspect.getmembers(module):
                    if inspect.isclass(obj) and issubclass(obj, Base) and obj != Base:
                        # Extract table comment
                        comment = None
                        if hasattr(obj, "__table_args__"):
                            if (
                                isinstance(obj.__table_args__, dict)
                                and "comment" in obj.__table_args__
                            ):
                                comment = obj.__table_args__["comment"]
                            elif isinstance(obj.__table_args__, tuple):
                                for arg in obj.__table_args__:
                                    if isinstance(arg, dict) and "comment" in arg:
                                        comment = arg["comment"]
                                        break

                        # Use class docstring as fallback
                        if not comment and obj.__doc__:
                            comment = obj.__doc__.strip()

                        # Generate singular and plural forms
                        singular = name
                        plural = inflection.plural(name)

                        # Initialize entity entry
                        entity_data = {
                            "meta": {
                                "singular": singular,
                                "plural": plural,
                                "comment": comment
                                or f"Represents a {singular.lower()}",
                            },
                            "properties": {
                                "id": {
                                    "column_name": "id",
                                    "comment": "Unique identifier",
                                }
                            },
                            "endpoints": {},
                        }

                        # Extract property comments from column definitions
                        source_code = inspect.getsource(obj)
                        for line in source_code.split("\n"):
                            # Look for column definitions
                            match = column_pattern.search(line.strip())
                            if match:
                                column_name = match.group(1)
                                # Skip id, we already added it
                                if column_name == "id":
                                    continue

                                # Generate a basic comment based on the column name
                                column_comment = " ".join(
                                    stringcase.capitalcase(word)
                                    for word in re.split(r"_", column_name)
                                )

                                entity_data["properties"][column_name] = {
                                    "column_name": column_name,
                                    "comment": column_comment,
                                }

                        # Add to schema docs
                        schema_docs[domain][name] = entity_data
            except Exception as e:
                logger.error(f"Error processing module {module_name}: {e}")

    return schema_docs


# Helper function to update entity definitions
def update_entity_definition(
    domain,
    entity_name,
    singular=None,
    plural=None,
    comment=None,
    properties=None,
    locale="en",
):
    """
    Update or create an entity definition in the localization file.

    Args:
        domain: Domain name
        entity_name: Entity class name
        singular: Singular form of the entity name
        plural: Plural form of the entity name
        comment: Entity description
        properties: Dictionary of property definitions
        locale: Locale code to update

    Returns:
        True if successful, False otherwise
    """
    locale_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        f"docs.{locale}.json",
    )

    # Load existing locale file if it exists
    if os.path.exists(locale_file):
        with open(locale_file, "r", encoding="utf-8") as f:
            try:
                locale_data = json.load(f)
            except json.JSONDecodeError:
                locale_data = {}
    else:
        locale_data = {}

    # Ensure domain exists
    if domain not in locale_data:
        locale_data[domain] = {}

    # Get or create entity
    if entity_name not in locale_data[domain]:
        locale_data[domain][entity_name] = {
            "meta": {
                "singular": singular or entity_name,
                "plural": plural or f"{entity_name}s",
                "comment": comment or f"Represents a {entity_name.lower()}",
            },
            "properties": {"id": {"column_name": "id", "comment": "Unique identifier"}},
            "endpoints": {},
        }

    # Update meta information if provided
    if singular or plural or comment:
        if "meta" not in locale_data[domain][entity_name]:
            locale_data[domain][entity_name]["meta"] = {}

        if singular:
            locale_data[domain][entity_name]["meta"]["singular"] = singular

        if plural:
            locale_data[domain][entity_name]["meta"]["plural"] = plural

        if comment:
            locale_data[domain][entity_name]["meta"]["comment"] = comment

    # Update properties if provided
    if properties:
        if "properties" not in locale_data[domain][entity_name]:
            locale_data[domain][entity_name]["properties"] = {}

        for prop_name, prop_def in properties.items():
            locale_data[domain][entity_name]["properties"][prop_name] = prop_def

    # Write updated data back to file
    try:
        with open(locale_file, "w", encoding="utf-8") as f:
            json.dump(locale_data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"Error updating entity definition: {e}")
        return False


# For command-line usage
if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Localization utilities")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Command to generate schema docs
    gen_parser = subparsers.add_parser("generate", help="Generate schema docs")
    gen_parser.add_argument("--locale", default="en", help="Locale code (default: en)")
    gen_parser.add_argument(
        "--output", default="docs.{locale}.json", help="Output file pattern"
    )

    # Command to list available locales
    list_parser = subparsers.add_parser("list", help="List available locales")

    # Command to update an entity definition
    update_parser = subparsers.add_parser("update", help="Update entity definition")
    update_parser.add_argument("--domain", required=True, help="Domain name")
    update_parser.add_argument("--entity", required=True, help="Entity name")
    update_parser.add_argument("--singular", help="Singular form")
    update_parser.add_argument("--plural", help="Plural form")
    update_parser.add_argument("--comment", help="Entity description")
    update_parser.add_argument(
        "--property",
        action="append",
        nargs=3,
        metavar=("NAME", "COLUMN", "COMMENT"),
        help="Property definition (can be used multiple times)",
    )
    update_parser.add_argument(
        "--locale", default="en", help="Locale code (default: en)"
    )

    args = parser.parse_args()

    if args.command == "generate":
        # Generate schema docs and save to file
        schema_docs = generate_schema_docs()
        output_file = args.output.format(locale=args.locale)

        # Check if file exists and merge with existing content
        try:
            with open(output_file, "r", encoding="utf-8") as f:
                existing = json.load(f)

            # Deep merge
            def merge(a, b):
                for key in b:
                    if (
                        key in a
                        and isinstance(a[key], dict)
                        and isinstance(b[key], dict)
                    ):
                        merge(a[key], b[key])
                    else:
                        a[key] = b[key]
                return a

            schema_docs = merge(existing, schema_docs)
        except (FileNotFoundError, json.JSONDecodeError):
            pass  # Use generated docs as is

        # Write to file
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(schema_docs, f, indent=2, ensure_ascii=False)

        logger.info(f"Documentation written to {output_file}")

    elif args.command == "list":
        # List available locales
        loc = Localization()
        locales = loc.get_available_locales()
        logger.info("Available locales:")
        for locale in locales:
            logger.info(f"  - {locale}")

    elif args.command == "update":
        # Update an entity definition
        properties = {}
        if args.property:
            for name, column, comment in args.property:
                properties[name] = {"column_name": column, "comment": comment}

        success = update_entity_definition(
            domain=args.domain,
            entity_name=args.entity,
            singular=args.singular,
            plural=args.plural,
            comment=args.comment,
            properties=properties,
            locale=args.locale,
        )

        if success:
            logger.info(
                f"Updated entity definition for {args.entity} in domain {args.domain}"
            )
        else:
            logger.error("Failed to update entity definition")
    else:
        parser.print_help()
