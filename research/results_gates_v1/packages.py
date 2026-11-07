"""Paper-only, relay-ineligible draft strategy packages.

Main YAML is a quoted-scalar subset plus SHA-linked extras. Nothing is applied.
Over-cap lead sets are rejected without truncation.
"""

from __future__ import annotations

import json

from .validate import SchemaError, sha256_bytes

MAX_LEADS = 10
_PLAIN = {"true": True, "false": False, "null": None}


def build(drafts: list[dict], extras: dict, labels: dict) -> dict:
    exploratory = set(labels["exploratory_two_month_only"])
    invalid_fills = set(labels["invalid_for_fills"])
    eligible = []
    withheld = []
    for draft in drafts:
        member = draft["family_member_id"]
        reason = None
        if member in exploratory:
            reason = "EXPLORATORY_TWO_MONTH_ONLY"
        elif member in invalid_fills:
            reason = "INVALID_FOR_FILLS"
        elif draft["definition"]["paper_only"] is not True or draft["definition"]["relay_eligible"] is not False:
            reason = "NOT_PAPER_ONLY"
        if reason:
            withheld.append({"strategy_id": draft["strategy_id"], "reason": reason})
            continue
        eligible.append(draft)
    if len(eligible) > MAX_LEADS:
        return {
            "status": "REJECTED_OVER_CAP",
            "attempted_leads": len(eligible),
            "emitted": [],
            "truncated": False,
            "withheld": withheld,
            "application": "NOT_APPLIED",
            "paper_only": True,
            "relay_eligible": False,
        }
    emitted = []
    for draft in eligible:
        extra_name = draft["extra_name"]
        if extra_name not in extras:
            raise SchemaError("missing draft extra")
        content = extras[extra_name].encode("utf-8")
        digest = sha256_bytes(content)
        document = {
            "schema": "strategy_draft_package_v1",
            "version": "1",
            "strategy_id": draft["strategy_id"],
            "family_member_id": draft["family_member_id"],
            "paper_only": True,
            "relay_eligible": False,
            "application": "NOT_APPLIED",
            "scope_label": "SYNTHETIC_FIXTURE",
            "definition": {
                "members": list(draft["definition"]["members"]),
                "ordered": draft["definition"]["ordered"],
            },
            "extras": [{"name": extra_name, "sha256": digest}],
        }
        yaml_text = dump_yaml(document)
        parsed = parse_yaml(yaml_text)
        if parsed != document:
            raise SchemaError("package roundtrip")
        if parsed["relay_eligible"] is not False or parsed["application"] != "NOT_APPLIED":
            raise SchemaError("package flags")
        emitted.append(
            {
                "strategy_id": draft["strategy_id"],
                "yaml": yaml_text,
                "sha256": sha256_bytes(yaml_text.encode("utf-8")),
                "extra_sha256": digest,
                "application": "NOT_APPLIED",
                "paper_only": True,
                "relay_eligible": False,
            }
        )
    return {
        "status": "EMITTED_DRAFTS",
        "attempted_leads": len(eligible),
        "emitted": emitted,
        "truncated": False,
        "withheld": withheld,
        "application": "NOT_APPLIED",
        "paper_only": True,
        "relay_eligible": False,
    }


def dump_yaml(obj) -> str:
    return "\n".join(_lines(obj, 0)) + "\n"


def parse_yaml(text: str):
    lines = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent % 2:
            raise SchemaError("yaml indent")
        lines.append((indent // 2, raw.strip()))
    position = 0

    def parse_block(level: int):
        nonlocal position
        if position >= len(lines):
            raise SchemaError("yaml eof")
        if lines[position][1].startswith("- "):
            return parse_list(level)
        return parse_map(level)

    def parse_map(level: int):
        nonlocal position
        mapped = {}
        while position < len(lines):
            indent, content = lines[position]
            if indent < level:
                break
            if indent != level or content.startswith("- "):
                raise SchemaError("yaml map")
            position += 1
            key, separator, rest = content.partition(":")
            if separator != ":":
                raise SchemaError("yaml key")
            rest = rest.strip()
            if rest == "":
                mapped[key] = parse_block(level + 1)
            elif rest == "{}":
                mapped[key] = {}
            elif rest == "[]":
                mapped[key] = []
            else:
                mapped[key] = _scalar(rest)
        return mapped

    def parse_list(level: int):
        nonlocal position
        items = []
        while position < len(lines):
            indent, content = lines[position]
            if indent < level:
                break
            if indent != level or not content.startswith("- "):
                raise SchemaError("yaml list")
            position += 1
            item = content[2:].strip()
            if item == "":
                items.append(parse_block(level + 1))
                continue
            if item.startswith('"') or item in _PLAIN or _is_int(item):
                items.append(_scalar(item))
                continue
            key, separator, rest = item.partition(":")
            if separator != ":":
                raise SchemaError("yaml list item")
            row = {}
            rest = rest.strip()
            row[key] = parse_block(level + 2) if rest == "" else _scalar(rest)
            while position < len(lines) and lines[position][0] == level + 1 and not lines[position][1].startswith("- "):
                _indent, continuation = lines[position]
                position += 1
                ckey, csep, crest = continuation.partition(":")
                if csep != ":":
                    raise SchemaError("yaml continuation")
                crest = crest.strip()
                row[ckey] = parse_block(level + 2) if crest == "" else _scalar(crest)
            items.append(row)
        return items

    value = parse_block(0)
    if position != len(lines):
        raise SchemaError("yaml trailing")
    return value


def _lines(obj, indent: int) -> list[str]:
    pad = "  " * indent
    if isinstance(obj, dict):
        rows = []
        for key, value in obj.items():
            if isinstance(value, (dict, list)) and value:
                rows.append(f"{pad}{key}:")
                rows.extend(_lines(value, indent + 1))
            elif isinstance(value, dict):
                rows.append(f"{pad}{key}: {{}}")
            elif isinstance(value, list):
                rows.append(f"{pad}{key}: []")
            else:
                rows.append(f"{pad}{key}: {_emit_scalar(value)}")
        return rows
    if isinstance(obj, list):
        rows = []
        for item in obj:
            if isinstance(item, dict):
                keys = list(item)
                first = keys[0]
                first_value = item[first]
                if isinstance(first_value, (dict, list)) and first_value:
                    rows.append(f"{pad}- {first}:")
                    rows.extend(_lines(first_value, indent + 2))
                else:
                    rendered = "{}" if first_value == {} else "[]" if first_value == [] else _emit_scalar(first_value)
                    rows.append(f"{pad}- {first}: {rendered}")
                for key in keys[1:]:
                    value = item[key]
                    if isinstance(value, (dict, list)) and value:
                        rows.append(f"{pad}  {key}:")
                        rows.extend(_lines(value, indent + 2))
                    elif isinstance(value, dict):
                        rows.append(f"{pad}  {key}: {{}}")
                    elif isinstance(value, list):
                        rows.append(f"{pad}  {key}: []")
                    else:
                        rows.append(f"{pad}  {key}: {_emit_scalar(value)}")
            else:
                rows.append(f"{pad}- {_emit_scalar(item)}")
        return rows
    raise SchemaError("yaml value")


def _emit_scalar(value) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    raise SchemaError("yaml scalar")


def _scalar(token: str):
    if token in _PLAIN:
        return _PLAIN[token]
    if token.startswith('"'):
        return json.loads(token)
    if _is_int(token):
        return int(token)
    raise SchemaError("yaml scalar")


def _is_int(token: str) -> bool:
    if token.startswith("-"):
        token = token[1:]
    return token.isdigit() and (token == "0" or not token.startswith("0"))
