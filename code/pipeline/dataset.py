"""Loading of the participant-facing CSV files under dataset/."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def dataset_dir() -> Path:
    return repo_root() / "dataset"


def _read_csv(name: str) -> List[dict]:
    path = dataset_dir() / name
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


@dataclass(frozen=True)
class Request:
    case_id: str
    email: str
    requested_at: str
    reported_status: str
    reported_at: str
    audit_at: str
    request_text: str


@dataclass(frozen=True)
class Identity:
    user_id: str
    name: str
    email: str
    phone: str
    email_hash: str
    updated_at: str


@dataclass(frozen=True)
class AuditLogEntry:
    audit_id: str
    user_id: str
    event_time: str
    field_name: str
    old_value: str
    new_value: str


@dataclass(frozen=True)
class Activity:
    activity_id: str
    event_time: str
    event_type: str
    source_system: str
    target_system: str
    resource_id: str
    reported_result: str
    details: str


@dataclass(frozen=True)
class EvidenceFile:
    file_id: str
    file_path: str
    origin: str
    captured_at: str


@dataclass(frozen=True)
class Rule:
    rule_id: str
    data_category: str
    condition: str
    required_action: str


def load_requests(name: str = "deletion_requests.csv") -> List[Request]:
    out = []
    for row in _read_csv(name):
        out.append(
            Request(
                case_id=row["case_id"].strip(),
                email=row["email"].strip(),
                requested_at=row.get("requested_at", "").strip(),
                reported_status=row.get("reported_status", "").strip(),
                reported_at=row.get("reported_at", "").strip(),
                audit_at=row.get("audit_at", "").strip(),
                request_text=row.get("request_text", ""),
            )
        )
    return out


def load_identities() -> List[Identity]:
    return [
        Identity(
            user_id=r["user_id"].strip(),
            name=r["name"].strip(),
            email=r["email"].strip(),
            phone=r["phone"].strip(),
            email_hash=r["email_hash"].strip(),
            updated_at=r["updated_at"].strip(),
        )
        for r in _read_csv("user_identities.csv")
    ]


def load_audit_log() -> List[AuditLogEntry]:
    return [
        AuditLogEntry(
            audit_id=r["audit_id"].strip(),
            user_id=r["user_id"].strip(),
            event_time=r["event_time"].strip(),
            field_name=r["field_name"].strip(),
            old_value=r["old_value"].strip(),
            new_value=r["new_value"].strip(),
        )
        for r in _read_csv("user_audit_log.csv")
    ]


def load_activity() -> List[Activity]:
    return [
        Activity(
            activity_id=r["activity_id"].strip(),
            event_time=r["event_time"].strip(),
            event_type=r["event_type"].strip(),
            source_system=r["source_system"].strip(),
            target_system=r["target_system"].strip(),
            resource_id=r["resource_id"].strip(),
            reported_result=r["reported_result"].strip(),
            details=r["details"],
        )
        for r in _read_csv("system_activity.csv")
    ]


def load_evidence_files() -> List[EvidenceFile]:
    return [
        EvidenceFile(
            file_id=r["file_id"].strip(),
            file_path=r["file_path"].strip(),
            origin=r["origin"].strip(),
            captured_at=r["captured_at"].strip(),
        )
        for r in _read_csv("evidence_files.csv")
    ]


def load_rules() -> List[Rule]:
    return [
        Rule(
            rule_id=r["rule_id"].strip(),
            data_category=r["data_category"].strip(),
            condition=r["condition"].strip(),
            required_action=r["required_action"].strip(),
        )
        for r in _read_csv("deletion_rules.csv")
    ]


def load_sample_output() -> Dict[str, dict]:
    return {r["case_id"]: r for r in _read_csv("sample_output.csv")}
