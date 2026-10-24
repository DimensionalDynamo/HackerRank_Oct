"""Shared helpers: map evidence vocabulary onto the policy data categories."""
from __future__ import annotations

import re
from typing import Optional, Tuple

from ..model import (
    CAT_ANALYTICS, CAT_BACKUP, CAT_CACHE, CAT_EMAIL_LOG, CAT_LEGAL_HOLD,
    CAT_MARKETING, CAT_SEARCH_INDEX, CAT_SECONDARY_IDENTIFIER,
    CAT_SECURITY_AUDIT, CAT_SUPPORT_ATTACHMENT, CAT_SUPPORT_MESSAGE,
    CAT_TELEMETRY, CAT_TOMBSTONE, CAT_USER_EXPORT, CAT_USER_PROFILE,
)

# Collection / table name -> category.  These names are the platform's own
# vocabulary and recur across JSON bundles and SQL snapshots.
COLLECTION_CATEGORY = {
    "customer_profiles": CAT_USER_PROFILE,
    "customer_profiles_primary": CAT_USER_PROFILE,
    "customer_profiles_failover_replica": CAT_USER_PROFILE,
    "profile_lookups": CAT_USER_PROFILE,
    "customer_search_documents": CAT_SEARCH_INDEX,
    "search_documents_primary": CAT_SEARCH_INDEX,
    "index_queries": CAT_SEARCH_INDEX,
    "customer_analytics_profiles": CAT_ANALYTICS,
    "customer_analytics_reports": CAT_ANALYTICS,
    "profiles": CAT_ANALYTICS,
    "support_records": CAT_SUPPORT_MESSAGE,
    "tickets": CAT_SUPPORT_MESSAGE,
    "customer_exports": CAT_USER_EXPORT,
    "exports": CAT_USER_EXPORT,
    "connector_subscriptions": "DATA_FLOW",
    "connectors": "DATA_FLOW",
    "backup_restore_entries": CAT_BACKUP,
    "restore_entries": CAT_BACKUP,
    "security_audit_records": CAT_SECURITY_AUDIT,
    "audit_records": CAT_SECURITY_AUDIT,
    "deleted_customer_references": CAT_TOMBSTONE,
    "marketing_profiles": CAT_MARKETING,
    "product_telemetry": CAT_TELEMETRY,
    "email_delivery_log": CAT_EMAIL_LOG,
    "legal_hold_records": CAT_LEGAL_HOLD,
    "cache_entries": CAT_CACHE,
}

# Explicit record types printed by the handover sheet / audit ledger.
RECORD_TYPE_CATEGORY = {
    "USER_PROFILE": CAT_USER_PROFILE,
    "SECONDARY_IDENTIFIER": CAT_SECONDARY_IDENTIFIER,
    "SEARCH_INDEX_ENTRY": CAT_SEARCH_INDEX,
    "CACHE_ENTRY": CAT_CACHE,
    "SUPPORT_MESSAGE": CAT_SUPPORT_MESSAGE,
    "SUPPORT_ATTACHMENT": CAT_SUPPORT_ATTACHMENT,
    "ANALYTICS_PROFILE": CAT_ANALYTICS,
    "PRODUCT_TELEMETRY": CAT_TELEMETRY,
    "USER_EXPORT": CAT_USER_EXPORT,
    "MARKETING_PROFILE": CAT_MARKETING,
    "EMAIL_DELIVERY_LOG": CAT_EMAIL_LOG,
    "SECURITY_AUDIT_LOG": CAT_SECURITY_AUDIT,
    "LEGAL_HOLD_RECORD": CAT_LEGAL_HOLD,
    "BACKUP_ARCHIVE": CAT_BACKUP,
    "ACCOUNT_TOMBSTONE": CAT_TOMBSTONE,
    "DATA_FLOW": "DATA_FLOW",
    "ACCOUNT": CAT_USER_PROFILE,
}

# resource_id prefix -> (system, category)
PREFIX_SYSTEM = {
    "account_": ("account_service", CAT_USER_PROFILE),
    "search_": ("search_service", CAT_SEARCH_INDEX),
    "analytics_": ("analytics_platform", CAT_ANALYTICS),
    "support_": ("support_platform", CAT_SUPPORT_MESSAGE),
    "export_": ("export_service", CAT_USER_EXPORT),
    "data_flow_": ("integration_service", "DATA_FLOW"),
    "backup_": ("backup_service", CAT_BACKUP),
    "audit_record_": ("security_service", CAT_SECURITY_AUDIT),
    "tombstone_": ("identity_service", CAT_TOMBSTONE),
    "marketing_": ("marketing_platform", CAT_MARKETING),
    "object_": ("object_storage", CAT_SUPPORT_ATTACHMENT),
    "cache_": ("cache_service", CAT_CACHE),
    "email_delivery_": ("email_delivery_service", CAT_EMAIL_LOG),
    "telemetry_": ("analytics_platform", CAT_TELEMETRY),
    "product_telemetry_": ("analytics_platform", CAT_TELEMETRY),
    "legal_hold_": ("legal_service", CAT_LEGAL_HOLD),
}

# System names as they appear in logs may be abbreviated/renamed.
SYSTEM_ALIASES = {
    "customer_account_service": "account_service",
    "profile_service": "account_service",
    "identity_service": "identity_service",
    "search_index_service": "search_service",
    "integration_management_service": "integration_service",
    "export_service": "export_service",
    "customer_export_service": "export_service",
    "object_store": "object_storage",
    "support_platform": "support_platform",
    "support_replica": "support_platform",
    "backup_service": "backup_service",
    "security_service": "security_service",
    "security_audit_service": "security_service",
    "analytics_platform": "analytics_platform",
    "marketing_platform": "marketing_platform",
    "account_service": "account_service",
    "downstream_delivery_service": "email_delivery_service",
    "notification_service": "notification_service",
    "email_delivery_service": "email_delivery_service",
    "cache_service": "cache_service",
    "data_catalog": "data_catalog",
    "legal_retention_service": "legal_service",
    "records_assurance_service": "records_assurance_service",
    "privacy_intake": "privacy_intake",
    "deletion_worker": "deletion_worker",
    "account_history_service": "account_history_service",
    "user_export_store": "export_service",
    "analytics_identity_store": "analytics_platform",
    "customer_search_service": "search_service",
    "identity_store": "identity_service",
    "contact_centre": "support_platform",
    "records_assurance_gateway": "records_assurance_gateway",
    "profile_store": "account_service",
    "object_storage": "object_storage",
}

DEFAULT_SYSTEM_FOR_CATEGORY = {
    CAT_USER_PROFILE: "account_service",
    CAT_SEARCH_INDEX: "search_service",
    CAT_ANALYTICS: "analytics_platform",
    CAT_SUPPORT_MESSAGE: "support_platform",
    CAT_SUPPORT_ATTACHMENT: "support_platform",
    CAT_USER_EXPORT: "export_service",
    CAT_BACKUP: "backup_service",
    CAT_SECURITY_AUDIT: "security_service",
    CAT_TOMBSTONE: "identity_service",
    CAT_MARKETING: "marketing_platform",
    CAT_CACHE: "cache_service",
    CAT_EMAIL_LOG: "email_delivery_service",
    CAT_TELEMETRY: "analytics_platform",
    CAT_LEGAL_HOLD: "legal_service",
    "DATA_FLOW": "integration_service",
}


def normalise_system(name: str) -> str:
    if not name:
        return ""
    key = name.strip().strip('"').lower()
    return SYSTEM_ALIASES.get(key, key)


def system_for_resource(resource_id: str) -> Tuple[str, str]:
    for prefix, (system, category) in PREFIX_SYSTEM.items():
        if resource_id.startswith(prefix):
            return system, category
    return "", ""


def category_for_collection(name: str) -> Optional[str]:
    if not name:
        return None
    key = name.strip().strip('"').lower()
    if key in COLLECTION_CATEGORY:
        return COLLECTION_CATEGORY[key]
    for suffix in ("_primary", "_read_replica", "_failover_replica",
                   "_reporting_replica", "_regional_replica"):
        if key.endswith(suffix):
            base = key[: -len(suffix)]
            if base in COLLECTION_CATEGORY:
                return COLLECTION_CATEGORY[base]
    return None


def category_for_record_type(value: str) -> Optional[str]:
    if not value:
        return None
    return RECORD_TYPE_CATEGORY.get(value.strip().strip('"').upper())


_SUFFIX_SYSTEM_RE = re.compile(r"^(?P<base>[a-z_]+)_(?:primary|read_replica|failover_replica|"
                               r"reporting_replica|regional_replica)$")


def strip_topology_suffix(table: str) -> str:
    m = _SUFFIX_SYSTEM_RE.match(table or "")
    return m.group("base") if m else (table or "")
