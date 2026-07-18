from __future__ import annotations

from collections.abc import Iterable

from correlis_schema import ActionTargetType, EntityType, OperationalActionType, RelationshipType
from pydantic import BaseModel, ConfigDict, Field, field_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IdentityKeyDefinition(FrozenModel):
    name: str = Field(min_length=1, max_length=128)
    fields: tuple[str, ...] = Field(min_length=1)
    description: str = Field(min_length=1, max_length=1024)

    @field_validator("fields")
    @classmethod
    def fields_must_be_non_blank(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("identity key fields must be non-blank")
        return value


class EntityTypeDefinition(FrozenModel):
    type: EntityType
    display_name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2048)
    identity_keys: tuple[IdentityKeyDefinition, ...] = Field(min_length=1)


class RelationshipTypeDefinition(FrozenModel):
    type: RelationshipType
    display_name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2048)
    source_types: tuple[EntityType, ...] = Field(min_length=1)
    target_types: tuple[EntityType, ...] = Field(min_length=1)
    directed: bool
    temporal: bool

    @field_validator("source_types", "target_types", mode="before")
    @classmethod
    def sort_entity_types(cls, value: Iterable[EntityType]) -> tuple[EntityType, ...]:
        return tuple(sorted(value, key=lambda item: item.value))


class ActionTypeDefinition(FrozenModel):
    type: OperationalActionType
    display_name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2048)
    target_types: tuple[ActionTargetType, ...] = Field(min_length=1)
    requires_reason: bool
    requires_evidence: bool
    emits_observation: bool

    @field_validator("target_types", mode="before")
    @classmethod
    def sort_target_types(cls, value: Iterable[ActionTargetType]) -> tuple[ActionTargetType, ...]:
        return tuple(sorted(value, key=lambda item: item.value))


class OntologyManifest(FrozenModel):
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    entity_types: tuple[EntityTypeDefinition, ...]
    relationship_types: tuple[RelationshipTypeDefinition, ...]
    action_types: tuple[ActionTypeDefinition, ...]
