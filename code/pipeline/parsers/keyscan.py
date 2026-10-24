"""Utilities for scanning heterogeneous evidence text into key/value events.

The corpus uses at least seven different logging layouts (logfmt, JSON lines,
RFC5424 syslog, pipe-delimited, Go-style runtime logs with an embedded quoted
blob, and plain business documents).  Rather than write one parser per layout
we normalise a line into:

    (timestamp, service, trace, {key: value, ...})

which is what every downstream rule actually needs.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)")
KV_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_.\-]*)=("(?:[^"\\]|\\.)*"|[^\s,|]+)')
JSON_OBJ_RE = re.compile(r"\{.*\}")


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        inner = value[1:-1]
        inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    return value


def _scan_kv(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, raw in KV_RE.findall(text):
        val = _unquote(raw)
        if key not in out:
            out[key] = val
    return out


def _unwrap_message(fields: Dict[str, str]) -> Dict[str, str]:
    """Go-style logs nest the real payload inside message="k=v k=v"."""
    msg = fields.pop("message", None)
    if not msg:
        return fields
    nested = _scan_kv(msg)
    for key, val in nested.items():
        fields.setdefault(key, val)
    return fields


def _service_from(fields: Dict[str, str], line: str) -> str:
    for key in ("service", "logger", "function", "system"):
        val = fields.get(key)
        if val:
            return val
    m = re.match(r"^\S+\s+(?:<[^>]*>\S+\s+)?\S+\s+(\S+)", line)
    if m and ("_" in m.group(1) or m.group(1).endswith("service")):
        return m.group(1)
    m = re.match(r"^\S+\s+\|\s*\w+\s*\|\s*([a-z_]+)\s*\|", line)
    if m:
        return m.group(1)
    m = re.match(r"^\S+\s+\[[A-Z]+\]\s+\[([a-z_]+)\]", line)
    if m:
        return m.group(1)
    return ""


def _trace_from(fields: Dict[str, str], line: str) -> str:
    for key in ("trace_id", "requestId", "request_id", "correlation_id"):
        val = fields.get(key)
        if val:
            return val
    m = re.search(r"\b(tr-[0-9a-z\-]+)\b", line)
    if m:
        return m.group(1)
    m = re.search(r"\b(rb-[0-9a-z\-]+)\b", line)
    if m:
        return m.group(1)
    return ""


def _event_from(fields: Dict[str, str], line: str) -> str:
    val = fields.get("event")
    if val:
        return val
    # pipe layout: | DEBUG | service | trace | event_name | k=v ...
    m = re.match(r"^\S+\s+\|\s*\w+\s*\|\s*[a-z_]+\s*\|\s*\S+\s*\|\s*([a-z_]+)\s*\|", line)
    if m:
        return m.group(1)
    # bracket layout: [DEBUG] [service] [trace] event_name: k=v
    m = re.match(r"^\S+\s+\[[A-Z]+\]\s+\[[a-z_]+\]\s+\[[^\]]*\]\s+([a-z_]+):", line)
    if m:
        return m.group(1)
    m = re.match(r"^\S+\s+\[\w+\]\s+\[[a-z_]+\]\s+\[[^\]]*\]\s+([a-z_]+)\b", line)
    if m:
        return m.group(1)
    m = re.search(r"\bevent=([A-Za-z0-9_\-]+)", line)
    if m:
        return m.group(1)
    return ""


def parse_line(line: str) -> Optional[dict]:
    """Normalise one log/document line into a structured event."""
    raw = line.rstrip("\n")
    if not raw.strip():
        return None

    fields = _scan_kv(raw)

    # embedded JSON object (structured logs / SQL payloads / JSON bundles)
    if raw.lstrip().startswith("{"):
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                for key, val in obj.items():
                    fields.setdefault(key, val if not isinstance(val, (dict, list)) else json.dumps(val))
                fields.setdefault("_json", "1")
        except Exception:
            pass
    else:
        m = JSON_OBJ_RE.search(raw)
        if m:
            try:
                obj = json.loads(m.group(0))
                if isinstance(obj, dict):
                    for key, val in obj.items():
                        fields.setdefault(key, val if not isinstance(val, (dict, list)) else json.dumps(val))
                    fields.setdefault("_json", "1")
            except Exception:
                pass

    fields = _unwrap_message(dict(fields))

    ts_match = TS_RE.match(raw)
    timestamp = ts_match.group(1) if ts_match else ""

    event = _event_from(fields, raw)
    service = _service_from(fields, raw)
    trace = _trace_from(fields, raw)

    return {
        "timestamp": timestamp,
        "service": service,
        "trace": trace,
        "event": event,
        "fields": fields,
        "raw": raw,
    }


def parse_text(text: str) -> List[dict]:
    out = []
    for line in text.splitlines():
        ev = parse_line(line)
        if ev is not None:
            out.append(ev)
    return out
