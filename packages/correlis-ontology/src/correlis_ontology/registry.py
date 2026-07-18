from __future__ import annotations

from types import MappingProxyType

from correlis_schema import (
    EntityRef,
    EntityType,
    Observation,
    OperationalAction,
    OperationalActionType,
    RelationshipType,
)

from .definitions import (
    ActionTypeDefinition,
    EntityTypeDefinition,
    OntologyManifest,
    RelationshipTypeDefinition,
)
from .errors import OntologyConfigurationError, OntologyValidationError


class OntologyRegistry:
    def __init__(
        self, *, name: str, version: str, entity_types, relationship_types, action_types
    ) -> None:
        self._name = name
        self._version = version
        self._entities = MappingProxyType(self._build_map(EntityType, entity_types, "entity"))
        self._relationships = MappingProxyType(
            self._build_map(RelationshipType, relationship_types, "relationship")
        )
        self._actions = MappingProxyType(
            self._build_map(OperationalActionType, action_types, "action")
        )
        for definition in self._entities.values():
            names = [item.name for item in definition.identity_keys]
            if len(names) != len(set(names)):
                raise OntologyConfigurationError(
                    f"duplicate identity keys for {definition.type.value}"
                )
        for definition in self._relationships.values():
            if not definition.source_types or not definition.target_types:
                raise OntologyConfigurationError(
                    f"relationship {definition.type.value} must have source and target types"
                )
        for definition in self._actions.values():
            if not definition.target_types:
                raise OntologyConfigurationError(
                    f"action {definition.type.value} must have target types"
                )

    @staticmethod
    def _build_map(enum_cls, definitions, label: str):
        result = {}
        for definition in definitions:
            if definition.type in result:
                raise OntologyConfigurationError(
                    f"duplicate {label} definition: {definition.type.value}"
                )
            result[definition.type] = definition
        missing = set(enum_cls) - set(result)
        extra = set(result) - set(enum_cls)
        if missing:
            raise OntologyConfigurationError(
                f"missing {label} definitions: {', '.join(sorted(i.value for i in missing))}"
            )
        if extra:
            raise OntologyConfigurationError(
                f"unknown {label} definitions: {', '.join(sorted(i.value for i in extra))}"
            )
        return result

    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> str:
        return self._version

    def manifest(self) -> OntologyManifest:
        return OntologyManifest(
            name=self._name,
            version=self._version,
            entity_types=tuple(
                self._entities[key] for key in sorted(self._entities, key=lambda i: i.value)
            ),
            relationship_types=tuple(
                self._relationships[key]
                for key in sorted(self._relationships, key=lambda i: i.value)
            ),
            action_types=tuple(
                self._actions[key] for key in sorted(self._actions, key=lambda i: i.value)
            ),
        )

    def get_entity_definition(self, entity_type: EntityType) -> EntityTypeDefinition:
        try:
            return self._entities[entity_type]
        except KeyError as exc:
            raise OntologyValidationError(
                "entity_type_not_registered",
                "entity type is not registered",
                entity_type=entity_type,
            ) from exc

    def get_relationship_definition(
        self, relationship_type: RelationshipType
    ) -> RelationshipTypeDefinition:
        try:
            return self._relationships[relationship_type]
        except KeyError as exc:
            raise OntologyValidationError(
                "relationship_type_not_registered",
                "relationship type is not registered",
                relationship_type=relationship_type,
            ) from exc

    def get_action_definition(self, action_type: OperationalActionType) -> ActionTypeDefinition:
        try:
            return self._actions[action_type]
        except KeyError as exc:
            raise OntologyValidationError(
                "action_type_not_registered",
                "action type is not registered",
                action_type=action_type,
            ) from exc

    def validate_entity(self, entity: EntityRef) -> None:
        self.get_entity_definition(entity.type)

    def validate_edge(
        self, relationship_type: RelationshipType, source: EntityRef, target: EntityRef
    ) -> None:
        self.validate_entity(source)
        self.validate_entity(target)
        definition = self.get_relationship_definition(relationship_type)
        if source.type not in definition.source_types:
            raise OntologyValidationError(
                "relationship_source_type_not_allowed",
                "relationship source type is not allowed",
                relationship_type=relationship_type,
                source_type=source.type,
            )
        if target.type not in definition.target_types:
            raise OntologyValidationError(
                "relationship_target_type_not_allowed",
                "relationship target type is not allowed",
                relationship_type=relationship_type,
                target_type=target.type,
            )

    def validate_observation(self, observation: Observation) -> None:
        self.validate_entity(observation.subject)
        if observation.object is not None:
            self.validate_entity(observation.object)
        if observation.relationship is not None and observation.object is not None:
            self.validate_edge(observation.relationship, observation.subject, observation.object)

    def validate_action(self, action: OperationalAction) -> None:
        definition = self.get_action_definition(action.type)
        if action.target.type not in definition.target_types:
            raise OntologyValidationError(
                "action_target_type_not_allowed",
                "action target type is not allowed",
                action_type=action.type,
                target_type=action.target.type,
            )
        if definition.requires_evidence and not action.evidence:
            raise OntologyValidationError(
                "action_evidence_required", "action evidence is required", action_type=action.type
            )
        if definition.requires_reason and (action.reason is None or not action.reason.strip()):
            raise OntologyValidationError(
                "action_reason_required", "action reason is required", action_type=action.type
            )
