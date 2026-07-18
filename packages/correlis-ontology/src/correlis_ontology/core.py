from __future__ import annotations

from correlis_schema import (
    ActionTargetType as AT,
)
from correlis_schema import (
    EntityType as E,
)
from correlis_schema import (
    OperationalActionType as A,
)
from correlis_schema import (
    RelationshipType as R,
)

from .definitions import (
    ActionTypeDefinition,
    EntityTypeDefinition,
    IdentityKeyDefinition,
    RelationshipTypeDefinition,
)
from .registry import OntologyRegistry

NAME = "correlis-core"
VERSION = "0.1.0"


def key(name: str, fields: tuple[str, ...], description: str) -> IdentityKeyDefinition:
    return IdentityKeyDefinition(name=name, fields=fields, description=description)


ENTITY_DEFINITIONS = (
    EntityTypeDefinition(
        type=E.ASSET,
        display_name="Asset",
        description=(
            "A host, device, or managed compute asset. Identity candidates are descriptive "
            "inputs for a future attributable entity-resolution projection and do not merge "
            "records automatically."
        ),
        identity_keys=(
            key("asset_id", ("asset_id",), "Provider or inventory asset identifier."),
            key("hostname", ("hostname",), "Observed host name."),
            key(
                "cloud_instance",
                ("cloud_provider", "cloud_account_id", "instance_id"),
                "Composite cloud instance identifier.",
            ),
        ),
    ),
    EntityTypeDefinition(
        type=E.APPLICATION,
        display_name="Application",
        description="An application, service, or workload endpoint.",
        identity_keys=(
            key("application_id", ("application_id",), "Application identifier."),
            key("service_name", ("service_name",), "Service name."),
            key("service_endpoint", ("scheme", "host", "port"), "Composite service endpoint."),
        ),
    ),
    EntityTypeDefinition(
        type=E.IDENTITY,
        display_name="Identity",
        description="A user, service principal, or account identity.",
        identity_keys=(
            key("identity_id", ("identity_id",), "Identity provider identifier."),
            key("principal_name", ("principal_name",), "Principal or login name."),
            key("sid", ("sid",), "Security identifier."),
        ),
    ),
    EntityTypeDefinition(
        type=E.PROCESS,
        display_name="Process",
        description="An executing process. Process ID alone is not a stable identity candidate.",
        identity_keys=(
            key("process_guid", ("process_guid",), "Process GUID."),
            key(
                "host_process_start",
                ("host_id", "process_id", "start_time"),
                "Composite host process start identity.",
            ),
        ),
    ),
    EntityTypeDefinition(
        type=E.NETWORK_ENDPOINT,
        display_name="Network endpoint",
        description="A transport socket endpoint.",
        identity_keys=(
            key("socket", ("address", "port", "transport"), "Composite network socket."),
        ),
    ),
    EntityTypeDefinition(
        type=E.CLOUD_RESOURCE,
        display_name="Cloud resource",
        description="A cloud resource in a provider account.",
        identity_keys=(
            key(
                "cloud_resource",
                ("provider", "account_id", "resource_id"),
                "Composite cloud resource identity.",
            ),
        ),
    ),
    EntityTypeDefinition(
        type=E.VULNERABILITY,
        display_name="Vulnerability",
        description="A vulnerability or exposure finding identifier.",
        identity_keys=(
            key("vulnerability_id", ("vulnerability_id",), "Vulnerability identifier."),
            key("cve", ("cve",), "CVE identifier."),
        ),
    ),
    EntityTypeDefinition(
        type=E.IP_ADDRESS,
        display_name="IP address",
        description="An IPv4 or IPv6 address.",
        identity_keys=(key("address", ("address",), "IP address string."),),
    ),
    EntityTypeDefinition(
        type=E.DOMAIN,
        display_name="Domain",
        description="A DNS domain name.",
        identity_keys=(key("fqdn", ("fqdn",), "Fully-qualified domain name."),),
    ),
    EntityTypeDefinition(
        type=E.FILE,
        display_name="File",
        description="A file artifact.",
        identity_keys=(
            key("sha256", ("sha256",), "SHA-256 file hash."),
            key("host_path", ("host_id", "path"), "Composite host path."),
        ),
    ),
    EntityTypeDefinition(
        type=E.CERTIFICATE,
        display_name="Certificate",
        description="A digital certificate artifact.",
        identity_keys=(
            key("sha256_fingerprint", ("sha256_fingerprint",), "Certificate SHA-256 fingerprint."),
            key(
                "issuer_serial", ("issuer", "serial_number"), "Composite issuer and serial number."
            ),
        ),
    ),
    EntityTypeDefinition(
        type=E.DATA_STORE,
        display_name="Data store",
        description="A database, bucket, share, or storage service.",
        identity_keys=(
            key("data_store_id", ("data_store_id",), "Data store identifier."),
            key("host_name", ("host_id", "name"), "Composite hosted data store name."),
        ),
    ),
)

RELATIONSHIP_DEFINITIONS = (
    RelationshipTypeDefinition(
        type=R.HAS_VULNERABILITY,
        display_name="Has vulnerability",
        description="Source currently has target vulnerability.",
        source_types=frozenset(
            {E.ASSET, E.APPLICATION, E.CLOUD_RESOURCE, E.NETWORK_ENDPOINT, E.DATA_STORE}
        ),
        target_types=frozenset({E.VULNERABILITY}),
        directed=True,
        temporal=True,
    ),
    RelationshipTypeDefinition(
        type=R.TARGETED,
        display_name="Targeted",
        description="Source targeted destination object.",
        source_types=frozenset(
            {E.IP_ADDRESS, E.DOMAIN, E.NETWORK_ENDPOINT, E.IDENTITY, E.ASSET, E.APPLICATION}
        ),
        target_types=frozenset(
            {E.ASSET, E.APPLICATION, E.IDENTITY, E.NETWORK_ENDPOINT, E.CLOUD_RESOURCE, E.DATA_STORE}
        ),
        directed=True,
        temporal=True,
    ),
    RelationshipTypeDefinition(
        type=R.COMMUNICATES_WITH,
        display_name="Communicates with",
        description="Source communicated with target.",
        source_types=frozenset(
            {E.PROCESS, E.ASSET, E.APPLICATION, E.NETWORK_ENDPOINT, E.CLOUD_RESOURCE}
        ),
        target_types=frozenset(
            {E.DOMAIN, E.IP_ADDRESS, E.NETWORK_ENDPOINT, E.ASSET, E.APPLICATION, E.CLOUD_RESOURCE}
        ),
        directed=True,
        temporal=True,
    ),
    RelationshipTypeDefinition(
        type=R.RUNS_ON,
        display_name="Runs on",
        description="Source runs on target infrastructure.",
        source_types=frozenset({E.PROCESS, E.APPLICATION, E.DATA_STORE}),
        target_types=frozenset({E.ASSET, E.CLOUD_RESOURCE}),
        directed=True,
        temporal=True,
    ),
    RelationshipTypeDefinition(
        type=R.SPAWNED,
        display_name="Spawned",
        description="Source process spawned target process.",
        source_types=frozenset({E.PROCESS}),
        target_types=frozenset({E.PROCESS}),
        directed=True,
        temporal=True,
    ),
    RelationshipTypeDefinition(
        type=R.AUTHENTICATED_TO,
        display_name="Authenticated to",
        description="Source authenticated to target.",
        source_types=frozenset({E.IDENTITY, E.ASSET, E.APPLICATION, E.PROCESS}),
        target_types=frozenset({E.ASSET, E.APPLICATION, E.CLOUD_RESOURCE, E.DATA_STORE}),
        directed=True,
        temporal=True,
    ),
    RelationshipTypeDefinition(
        type=R.ACCESSED,
        display_name="Accessed",
        description="Source accessed target.",
        source_types=frozenset({E.IDENTITY, E.PROCESS, E.APPLICATION, E.ASSET}),
        target_types=frozenset({E.FILE, E.DATA_STORE, E.APPLICATION, E.CLOUD_RESOURCE}),
        directed=True,
        temporal=True,
    ),
    RelationshipTypeDefinition(
        type=R.RESOLVED_TO,
        display_name="Resolved to",
        description="Domain resolved to IP address.",
        source_types=frozenset({E.DOMAIN}),
        target_types=frozenset({E.IP_ADDRESS}),
        directed=True,
        temporal=True,
    ),
    RelationshipTypeDefinition(
        type=R.EXPLOITED,
        display_name="Exploited",
        description="Attack source exploited target.",
        source_types=frozenset(
            {
                E.IP_ADDRESS,
                E.DOMAIN,
                E.NETWORK_ENDPOINT,
                E.IDENTITY,
                E.ASSET,
                E.APPLICATION,
                E.PROCESS,
            }
        ),
        target_types=frozenset(
            {E.ASSET, E.APPLICATION, E.CLOUD_RESOURCE, E.NETWORK_ENDPOINT, E.DATA_STORE}
        ),
        directed=True,
        temporal=True,
    ),
    RelationshipTypeDefinition(
        type=R.COMPROMISED,
        display_name="Compromised",
        description="Source or attack actor compromised target.",
        source_types=frozenset(
            {
                E.IP_ADDRESS,
                E.DOMAIN,
                E.NETWORK_ENDPOINT,
                E.IDENTITY,
                E.ASSET,
                E.APPLICATION,
                E.PROCESS,
            }
        ),
        target_types=frozenset(
            {E.ASSET, E.APPLICATION, E.IDENTITY, E.CLOUD_RESOURCE, E.NETWORK_ENDPOINT, E.DATA_STORE}
        ),
        directed=True,
        temporal=True,
    ),
    RelationshipTypeDefinition(
        type=R.MOVED_LATERALLY_TO,
        display_name="Moved laterally to",
        description="Source moved laterally to target.",
        source_types=frozenset({E.ASSET, E.APPLICATION, E.IDENTITY, E.PROCESS}),
        target_types=frozenset({E.ASSET, E.APPLICATION, E.CLOUD_RESOURCE, E.DATA_STORE}),
        directed=True,
        temporal=True,
    ),
)


def action(t, targets, reason):
    return ActionTypeDefinition(
        type=t,
        display_name=t.value.replace("_", " ").title(),
        description=f"Operational action policy for {t.value}.",
        target_types=frozenset(targets),
        requires_reason=reason,
        requires_evidence=True,
        emits_observation=True,
    )


ACTION_DEFINITIONS = (
    action(A.CONFIRM_RELATIONSHIP, {AT.RELATIONSHIP}, True),
    action(A.REJECT_RELATIONSHIP, {AT.RELATIONSHIP}, True),
    action(A.MARK_CONTAINED, {AT.ENTITY, AT.ATTACK_SCENE, AT.INCIDENT}, True),
    action(A.ASSIGN_OWNER, {AT.ATTACK_SCENE, AT.INCIDENT, AT.REMEDIATION_TASK}, False),
    action(
        A.REQUEST_EVIDENCE,
        {AT.ENTITY, AT.RELATIONSHIP, AT.ATTACK_SCENE, AT.INCIDENT, AT.OBSERVATION},
        True,
    ),
    action(A.SUPPRESS_RELATIONSHIP, {AT.RELATIONSHIP}, True),
    action(A.RERUN_RULE, {AT.RULE, AT.ATTACK_SCENE}, True),
    action(A.EXPORT_EVIDENCE, {AT.EVIDENCE, AT.OBSERVATION, AT.ATTACK_SCENE, AT.INCIDENT}, False),
    action(
        A.OPEN_REMEDIATION_TASK, {AT.ENTITY, AT.RELATIONSHIP, AT.ATTACK_SCENE, AT.INCIDENT}, True
    ),
    action(A.RECORD_CONTAINMENT_DECISION, {AT.ENTITY, AT.ATTACK_SCENE, AT.INCIDENT}, True),
)

CORE_ONTOLOGY = OntologyRegistry(
    name=NAME,
    version=VERSION,
    entity_types=ENTITY_DEFINITIONS,
    relationship_types=RELATIONSHIP_DEFINITIONS,
    action_types=ACTION_DEFINITIONS,
)
