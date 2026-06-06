#
# This file is part of VIRL 2
# Copyright (c) 2019-2026, Cisco Systems, Inc.
# All rights reserved.
#
from __future__ import annotations

from typing import ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from simple_common.schemas.enums import AuthMethod, ComputeState, ConfigRowName, OptInStatus
from simple_common.schemas.types import (
    DEFAULT_AUTH_METHOD,
    DEFAULT_COMPUTE_STATE,
    DEFAULT_OPT_IN,
    DEFAULT_OPT_IN_VERSION,
    DEFAULT_TOKEN_LIFETIME,
    RESOURCE_POOL_DESCRIPTION,
    MultiLineStr,
    OAuth2Timeout,
    OneLineStr,
    Timeout,
    UUID4Type,
)

_PERSIST_EXCLUDED_FIELDS: frozenset[str] = frozenset({"method"})


class ConfigModel(BaseModel):
    """Pydantic base for Configuration-row-backed auth/system config models."""

    model_config = ConfigDict(extra="forbid")

    _row_name: ClassVar[ConfigRowName]
    _secret_fields: ClassVar[frozenset[str]] = frozenset()
    _request_hidden_fields: ClassVar[frozenset[str]] = frozenset()
    # Credential field merged/preserved on PATCH import (subset of _secret_fields).
    _patch_credential_field: ClassVar[str | None] = None
    # Export fields normalized from "" to null on GET responses.
    _empty_export_fields: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def config_row_name(cls) -> ConfigRowName:
        """Configuration table row key for this model."""
        if "method" in cls.model_fields:
            return ConfigRowName(cls.model_fields["method"].default)
        return cls._row_name

    @classmethod
    def external_auth_method(cls) -> AuthMethod:
        """AuthMethod for external-auth config models (from method default)."""
        slug = cls.model_fields["method"].default
        return AuthMethod(slug)

    def to_public_dict(self) -> dict:
        return self.model_dump(
            mode="json",
            exclude=self._secret_fields | _PERSIST_EXCLUDED_FIELDS,
        )

    def to_persisted_dict(self) -> dict:
        return self.model_dump(mode="json", exclude=_PERSIST_EXCLUDED_FIELDS)

    @classmethod
    def from_dict(cls, values: dict) -> Self:
        """Load from DB JSON; omit keys stored as null."""
        filtered = {k: v for k, v in values.items() if v is not None}
        return cls.model_validate(filtered)


def stored_auth_config_is_present(
    model_cls: type[ConfigModel], stored: ConfigModel | None
) -> bool:
    """True when a persisted config row has non-empty indicator field(s).

    Empty placeholder rows (defaults only) count as not configured. Indicator
    fields match ``_empty_export_fields`` used on GET export normalization.
    """
    if stored is None:
        return False
    indicators = model_cls._empty_export_fields
    if not indicators:
        return True
    return any(
        (value := getattr(stored, name, ""))
        and (value.strip() if isinstance(value, str) else True)
        for name in indicators
    )


def resolve_config_patch_nulls(model_cls: type[ConfigModel], patch: dict) -> dict:
    """Map explicit null PATCH values to field defaults before merge."""
    resolved = dict(patch)
    for key, value in patch.items():
        if value is None and key in model_cls.model_fields:
            finfo = model_cls.model_fields[key]
            resolved[key] = finfo.get_default(call_default_factory=True)
    return resolved


def preserve_existing_secret(config: dict, field_name: str) -> dict:
    """Drop blank secret placeholders so PATCH does not clear stored credentials."""
    updated = dict(config)
    if updated.get(field_name) == "":
        updated.pop(field_name)
    return updated


def merge_config_patch(
    model_cls: type[ConfigModel],
    patch: dict,
    stored: ConfigModel | None = None,
    credential_field: str | None = None,
) -> ConfigModel:
    """Merge a PATCH import dict onto stored config (ciphertext representation).

    Applies null-to-default, preserves blank credential placeholders, and merges
    onto stored (or model defaults). Returns one merged model; secrets stay
    encrypted when taken from storage. Import callers split persist vs runtime
    configs and encrypt/decrypt using secret_in_patch.

    Not used for SSO discover/test inline bodies; see config_from_auth_request.
    """
    field = credential_field or model_cls._patch_credential_field
    resolved = resolve_config_patch_nulls(model_cls, patch)
    if field:
        resolved = preserve_existing_secret(resolved, field)
    merged = (stored or model_cls()).to_persisted_dict()
    merged.update(resolved)
    return model_cls.model_validate(merged)


def secret_in_patch(model_cls: type[ConfigModel], patch: dict) -> bool:
    """Return True when the patch carries a non-empty secret field value."""
    field = model_cls._patch_credential_field
    if field is None:
        return False
    resolved = resolve_config_patch_nulls(model_cls, patch)
    resolved = preserve_existing_secret(resolved, field)
    return field in resolved and bool(resolved[field])


def normalize_config_export_empty_fields(data: dict, keys: list[str]) -> dict:
    """Map blank strings to null for selected export fields only.

    Mutates data in place and returns the same dict.
    """
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip() == "":
            data[key] = None
    return data


class SystemConfig(ConfigModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    _row_name: ClassVar[ConfigRowName] = ConfigRowName.SYSTEM

    jwt_token_lifetime: int = DEFAULT_TOKEN_LIFETIME
    opt_in: OptInStatus = DEFAULT_OPT_IN
    opt_in_version: str = DEFAULT_OPT_IN_VERSION
    maintenance_mode: bool = False
    login_notice: str = ""
    new_host_state: ComputeState = DEFAULT_COMPUTE_STATE
    auth_method: AuthMethod = DEFAULT_AUTH_METHOD


_CML_GROUP = "cml:group"
_CML_ADMIN = "cml:admin"
_DEFAULT_ADMIN_VALUE = "^true$"


class LDAPConfig(ConfigModel):
    """LDAP auth method configuration."""

    _secret_fields: ClassVar[frozenset[str]] = frozenset({"manager_password", "manager_ntlm_hash"})
    _request_hidden_fields: ClassVar[frozenset[str]] = frozenset({"manager_ntlm_hash"})
    _patch_credential_field: ClassVar[str] = "manager_password"
    _empty_export_fields: ClassVar[tuple[str, ...]] = ("server_urls", "cert_data_pem")

    method: Literal["ldap"] = "ldap"
    server_urls: OneLineStr = Field(
        default="",
        max_length=256,
        description=(
            "URI of LDAP server, either LDAP or LDAPS, multiple servers can "
            "be specified, separate with space."
        ),
        examples=["ldaps://ad.corp.com:3269"],
    )
    verify_tls: bool = Field(
        default=True,
        description="Set to false if certificates should not be verified.",
    )
    cert_data_pem: MultiLineStr = Field(
        default="", description="Reference to a public certificate (PEM)."
    )
    use_ntlm: bool = Field(
        default=False,
        description=(
            "If true then the password for the manager user will be "
            "stored as NTLM hash. Only works with ActiveDirectory servers."
        ),
    )
    root_dn: OneLineStr = Field(
        default="",
        max_length=256,
        description="The root DN that will be applied.",
        examples=["DC=corp,DC=com"],
    )
    user_search_base: OneLineStr = Field(
        default="",
        max_length=256,
        description=(
            "The user search base where users should be looked up. "
            "Typically a OU or CN. Will be combined with the root DN."
        ),
        examples=["CN=users,CN=accounts"],
    )
    user_search_filter: OneLineStr = Field(
        default="",
        max_length=1024,
        description=(
            "The filter that will be applied to the user. Must have a "
            "placeholder {0} replaced with the username."
        ),
    )
    admin_search_filter: OneLineStr = Field(
        default="",
        max_length=1024,
        description=("Same as for the user search filter. Grants admin rights if matched."),
    )
    group_search_base: OneLineStr = Field(
        default="",
        max_length=256,
        description=(
            "The group search base where groups should be looked up. "
            "Typically a OU or CN. Will be combined with the root DN."
        ),
    )
    group_search_filter: OneLineStr = Field(
        default="",
        max_length=1024,
        description=(
            "The filter applied to groups. Must have a placeholder "
            "{0} replaced with the group name."
        ),
    )
    group_via_user: bool = Field(
        default=False,
        description=("If true, use group_user_attribute to determine user group memberships."),
    )
    group_user_attribute: OneLineStr = Field(
        default="",
        max_length=64,
        description="Attribute of the user that holds group memberships.",
    )
    group_membership_filter: OneLineStr = Field(
        default="",
        max_length=1024,
        description="Filter to apply to groups specifying the user.",
    )
    manager_dn: OneLineStr = Field(
        default="",
        max_length=256,
        description=("Manager user DN for lookup if anonymous search is not allowed."),
    )
    manager_password: OneLineStr = Field(
        default="",
        max_length=256,
        description=(
            "Password for the management user. If use_ntlm is true "
            "then the password will be converted to an NTLM hash and the "
            "hash is stored. Otherwise, the cleartext password will be "
            "stored using obfuscation."
        ),
    )
    manager_ntlm_hash: OneLineStr = Field(
        default="",
        max_length=256,
        description="NTLM hash derived from the manager password (DB only).",
    )
    timeout: Timeout
    display_attribute: OneLineStr = Field(
        default="",
        max_length=256,
        description="User attribute for displaying the logged in user.",
    )
    group_display_attribute: OneLineStr = Field(
        default="",
        max_length=256,
        description="Group attribute for displaying group description.",
    )
    email_address_attribute: OneLineStr = Field(
        default="",
        max_length=64,
        description="User attribute for displaying the email address.",
    )
    resource_pool: UUID4Type | None = Field(default=None, description=RESOURCE_POOL_DESCRIPTION)


class RadiusConfig(ConfigModel):
    """RADIUS auth method configuration."""

    _secret_fields: ClassVar[frozenset[str]] = frozenset({"secret"})
    _patch_credential_field: ClassVar[str] = "secret"
    _empty_export_fields: ClassVar[tuple[str, ...]] = ("server_hosts",)

    method: Literal["radius"] = "radius"
    server_hosts: OneLineStr = Field(
        default="",
        max_length=1024,
        description=(
            "Space-separated list of RADIUS servers. Each entry may be "
            "'host' or 'host:port'. Entries without ':port' use the global "
            "'port' value."
        ),
    )
    port: int = Field(
        default=1812,
        ge=1,
        le=65535,
        description="Default RADIUS server port (used when entry has no ':port').",
    )
    secret: OneLineStr = Field(
        default="",
        max_length=256,
        description="Shared secret for the RADIUS server(s).",
    )
    timeout: Timeout
    nas_identifier: OneLineStr | None = Field(
        default=None,
        max_length=256,
        description="NAS-Identifier to include in requests (optional).",
    )
    resource_pool: UUID4Type | None = Field(default=None, description=RESOURCE_POOL_DESCRIPTION)
    groups_key: OneLineStr = Field(
        default=_CML_GROUP,
        max_length=256,
        description="RADIUS attribute key used to read group memberships.",
    )
    permitted_key: OneLineStr = Field(
        default="",
        max_length=256,
        description="Optional RADIUS attribute key required for access.",
    )
    permitted_value: OneLineStr = Field(
        default="",
        max_length=1024,
        description="Optional regex/value expected for permitted_key.",
    )
    admin_key: OneLineStr = Field(
        default=_CML_ADMIN,
        max_length=256,
        description="RADIUS attribute key used to determine admin role.",
    )
    admin_value: OneLineStr = Field(
        default=_DEFAULT_ADMIN_VALUE,
        max_length=1024,
        description="Regex/value expected for admin_key to grant admin role.",
    )


class OIDCConfig(ConfigModel):
    """OpenID Connect auth method configuration."""

    _secret_fields: ClassVar[frozenset[str]] = frozenset({"client_secret"})
    _patch_credential_field: ClassVar[str] = "client_secret"
    _empty_export_fields: ClassVar[tuple[str, ...]] = ("issuer_url",)

    method: Literal["oidc"] = "oidc"
    issuer_url: OneLineStr = Field(default="", max_length=512)
    client_id: OneLineStr = Field(default="", max_length=256)
    client_secret: OneLineStr = Field(default="", max_length=1024)
    timeout: Timeout
    authorization_endpoint: OneLineStr = Field(default="", max_length=512)
    token_endpoint: OneLineStr = Field(default="", max_length=512)
    userinfo_endpoint: OneLineStr = Field(default="", max_length=512)
    jwks_uri: OneLineStr = Field(default="", max_length=512)
    redirect_uri_override: OneLineStr = Field(default="", max_length=512)
    scopes: OneLineStr = Field(default="openid profile email groups", max_length=256)
    username_claim: OneLineStr = Field(default="email", max_length=128)
    admin_claim: OneLineStr = Field(default="groups", max_length=128)
    admin_value: OneLineStr = Field(default="admin", max_length=256)
    groups_claim: OneLineStr = Field(default="groups", max_length=128)
    permitted_claim: OneLineStr = Field(default="", max_length=128)
    permitted_value: OneLineStr = Field(default="", max_length=256)
    resource_pool: UUID4Type | None = Field(default=None, description=RESOURCE_POOL_DESCRIPTION)


class OAuth2Config(ConfigModel):
    """OAuth 2.0 auth method configuration."""

    _secret_fields: ClassVar[frozenset[str]] = frozenset({"client_secret"})
    _patch_credential_field: ClassVar[str] = "client_secret"
    _empty_export_fields: ClassVar[tuple[str, ...]] = ("authorization_endpoint",)

    method: Literal["oauth2"] = "oauth2"
    provider: OneLineStr = Field(default="generic", max_length=64)
    timeout: OAuth2Timeout
    authorization_endpoint: OneLineStr = Field(default="", max_length=512)
    token_endpoint: OneLineStr = Field(default="", max_length=512)
    client_id: OneLineStr = Field(default="", max_length=256)
    client_secret: OneLineStr = Field(default="", max_length=1024)
    scopes: OneLineStr = Field(default="read:user read:org", max_length=256)
    user_info_endpoint: OneLineStr = Field(default="", max_length=512)
    groups_endpoint: OneLineStr = Field(default="", max_length=512)
    username_field: OneLineStr = Field(default="login", max_length=128)
    email_field: OneLineStr = Field(default="email", max_length=128)
    groups_field: OneLineStr = Field(default="login", max_length=128)
    admin_group: OneLineStr = Field(default="admin", max_length=256)
    permitted_claim: OneLineStr = Field(default="", max_length=128)
    permitted_value: OneLineStr = Field(default="", max_length=256)
    resource_pool: UUID4Type | None = Field(default=None, description=RESOURCE_POOL_DESCRIPTION)
    redirect_uri_override: OneLineStr = Field(default="", max_length=512)


class SAMLConfig(ConfigModel):
    """SAML 2.0 auth method configuration."""

    _secret_fields: ClassVar[frozenset[str]] = frozenset({"sp_private_key"})
    _patch_credential_field: ClassVar[str] = "sp_private_key"
    _empty_export_fields: ClassVar[tuple[str, ...]] = ("idp_sso_url",)

    method: Literal["saml"] = "saml"
    sp_entity_id: OneLineStr = Field(default="", max_length=512)
    sp_acs_url: OneLineStr = Field(default="", max_length=512)
    sp_sls_url: OneLineStr = Field(default="", max_length=512)
    sp_cert: MultiLineStr = Field(default="")
    sp_private_key: MultiLineStr = Field(default="")
    idp_entity_id: OneLineStr = Field(default="", max_length=512)
    idp_sso_url: OneLineStr = Field(default="", max_length=512)
    idp_slo_url: OneLineStr = Field(default="", max_length=512)
    idp_cert: MultiLineStr = Field(default="")
    username_attribute: OneLineStr = Field(default="email", max_length=128)
    email_attribute: OneLineStr = Field(default="email", max_length=128)
    groups_attribute: OneLineStr = Field(default="groups", max_length=128)
    admin_group: OneLineStr = Field(default="admin", max_length=256)
    permitted_attribute: OneLineStr = Field(default="", max_length=128)
    permitted_value: OneLineStr = Field(default="", max_length=256)
    want_assertions_signed: bool = Field(default=True)
    want_messages_signed: bool = Field(default=False)
    resource_pool: UUID4Type | None = Field(default=None, description=RESOURCE_POOL_DESCRIPTION)


_AUTH_CONFIG_CLASSES: tuple[type[ConfigModel], ...] = (
    LDAPConfig,
    RadiusConfig,
    OIDCConfig,
    OAuth2Config,
    SAMLConfig,
)
# Adding a method: register its ConfigModel here and wire it in registry.py.

AUTH_CONFIG_BY_METHOD: dict[AuthMethod, type[ConfigModel]] = {
    cls.external_auth_method(): cls for cls in _AUTH_CONFIG_CLASSES
}
