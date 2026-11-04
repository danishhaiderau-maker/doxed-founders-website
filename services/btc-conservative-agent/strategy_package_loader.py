"""Load and hard-refuse strategy package YAML specs (R47 tile-spec DRAFT).

Packages under ``strategies/<id>.yaml`` are discovered for dashboard registry
auto-render only. They are **never** activated into the frozen
``ACTIVE_TILE_REGISTRY`` / paper-execution lanes by this loader — each tile
still needs its own R22 + RK PASS.

Hard refuses (fail closed):
- schema-invalid / bad spec
- missing ``series_stamp`` (R52/P5)
- ``paper_only`` is not exactly true
- any credential / secret / key field present anywhere in the document
- evidence.status outside {LEAD, OOS_PASS, FWD_PASS}

R53(c): only ``evidence.status == FWD_PASS`` may feed the R53(c) gate helper.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

try:
    import yaml
except ImportError as exc:  # pragma: no cover - CI installs PyYAML via requirements
    raise RuntimeError("PyYAML is required to load strategy packages") from exc

try:
    import jsonschema
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("jsonschema is required to validate strategy packages") from exc


SCHEMA_NAME = "strategy_package_v1"
EVIDENCE_STATUSES = frozenset({"LEAD", "OOS_PASS", "FWD_PASS"})
R53C_ELIGIBLE_STATUS = "FWD_PASS"

# Credential / secret field names refused anywhere in the document (casefold).
_CREDENTIAL_KEY_EXACT = frozenset({
    "api_key", "api_secret", "apisecret", "apikey",
    "secret", "password", "passwd", "token",
    "credential", "credentials", "private_key", "privatekey",
    "access_key", "access_token", "secret_key", "secretkey",
    "bitfinex_api_key", "bitfinex_api_secret", "bitfinex_key", "bitfinex_secret",
    "bot_admin_token", "monitor_read_token", "laptop_monitor_read_token",
    "authorization", "bearer", "client_secret", "refresh_token",
})
_CREDENTIAL_KEY_RE = re.compile(
    r"(api[_-]?key|api[_-]?secret|private[_-]?key|access[_-]?token|"
    r"client[_-]?secret|refresh[_-]?token|password|passwd|"
    r"credential|bitfinex.*(key|secret)|secret[_-]?key)",
    re.IGNORECASE,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STRATEGIES_DIR = _REPO_ROOT / "strategies"
DEFAULT_SCHEMA_PATH = DEFAULT_STRATEGIES_DIR / "schema" / "strategy-package.schema.json"


class StrategyPackageRefuse(ValueError):
    """Hard-refuse a strategy package (paper-bot loader fail-closed)."""


def schema_path() -> Path:
    return DEFAULT_SCHEMA_PATH


def load_schema(path: Path | None = None) -> dict[str, Any]:
    p = path or schema_path()
    with p.open(encoding="utf-8") as fh:
        return json.load(fh)


def _is_credential_key(key: str) -> bool:
    k = str(key or "").strip()
    if not k:
        return False
    folded = k.casefold().replace("-", "_")
    if folded in _CREDENTIAL_KEY_EXACT:
        return True
    return bool(_CREDENTIAL_KEY_RE.search(k))


def find_credential_keys(obj: Any, *, path: str = "$") -> list[str]:
    """Return dotted paths of any credential-shaped keys in ``obj``."""
    hits: list[str] = []
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            key_s = str(key)
            here = f"{path}.{key_s}"
            if _is_credential_key(key_s):
                hits.append(here)
            hits.extend(find_credential_keys(value, path=here))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            hits.extend(find_credential_keys(item, path=f"{path}[{i}]"))
    return hits


def _require_mapping(raw: Any, *, source: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise StrategyPackageRefuse(f"{source}: package root must be a mapping")
    return dict(raw)


def validate_package_dict(
    raw: Mapping[str, Any],
    *,
    source: str = "<dict>",
    schema: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate + hard-refuse. Returns a plain dict copy on success."""
    data = _require_mapping(raw, source=source)

    creds = find_credential_keys(data)
    if creds:
        raise StrategyPackageRefuse(
            f"{source}: credential/secret field(s) refused: {', '.join(creds)}"
        )

    if "series_stamp" not in data or not str(data.get("series_stamp") or "").strip():
        raise StrategyPackageRefuse(f"{source}: series_stamp is required (R52/P5)")

    if data.get("paper_only") is not True:
        raise StrategyPackageRefuse(
            f"{source}: paper_only must be true (got {data.get('paper_only')!r})"
        )

    evidence = data.get("evidence")
    if not isinstance(evidence, Mapping):
        raise StrategyPackageRefuse(f"{source}: evidence must be an object")
    status = evidence.get("status")
    if status not in EVIDENCE_STATUSES:
        raise StrategyPackageRefuse(
            f"{source}: invalid evidence.status {status!r}; "
            f"allowed={sorted(EVIDENCE_STATUSES)}"
        )

    sch = schema if schema is not None else load_schema()
    try:
        jsonschema.validate(instance=data, schema=sch)
    except jsonschema.ValidationError as exc:
        raise StrategyPackageRefuse(
            f"{source}: bad spec — {exc.message} (path={list(exc.path)})"
        ) from exc

    out = dict(data)
    out["_schema"] = SCHEMA_NAME
    out["_source"] = source
    out["_activated"] = False  # never activated by loader alone
    return out


def feeds_r53c_gate(package: Mapping[str, Any]) -> bool:
    """R53(c): only FWD_PASS may feed the gate. LEAD / OOS_PASS refuse."""
    evidence = package.get("evidence") if isinstance(package, Mapping) else None
    if not isinstance(evidence, Mapping):
        return False
    return evidence.get("status") == R53C_ELIGIBLE_STATUS


def assert_r53c_eligible(package: Mapping[str, Any], *, source: str = "<package>") -> None:
    if not feeds_r53c_gate(package):
        status = None
        evidence = package.get("evidence") if isinstance(package, Mapping) else None
        if isinstance(evidence, Mapping):
            status = evidence.get("status")
        raise StrategyPackageRefuse(
            f"{source}: R53(c) gate refuses evidence.status={status!r}; "
            f"only {R53C_ELIGIBLE_STATUS} may feed R53(c)"
        )


def load_package_file(
    path: Path | str,
    *,
    schema: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise StrategyPackageRefuse(f"{p}: cannot read ({exc})") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise StrategyPackageRefuse(f"{p}: bad YAML ({exc})") from exc
    return validate_package_dict(raw, source=str(p), schema=schema)


def iter_strategy_yaml_paths(strategies_dir: Path | str | None = None) -> list[Path]:
    root = Path(strategies_dir) if strategies_dir is not None else DEFAULT_STRATEGIES_DIR
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.glob("*.yaml")
        if p.is_file() and not p.name.startswith("_")
    )


def load_registered_packages(
    strategies_dir: Path | str | None = None,
    *,
    schema: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Load every ``strategies/<id>.yaml``. Fail closed on the first refuse."""
    sch = schema if schema is not None else load_schema()
    packages: list[dict[str, Any]] = []
    for path in iter_strategy_yaml_paths(strategies_dir):
        packages.append(load_package_file(path, schema=sch))
    return packages


def build_dashboard_tile_specs(
    packages: list[Mapping[str, Any]] | None = None,
    *,
    strategies_dir: Path | str | None = None,
) -> list[dict[str, Any]]:
    """One dashboard tile card per registered package (never activated)."""
    pkgs = (
        list(packages)
        if packages is not None
        else load_registered_packages(strategies_dir)
    )
    tiles: list[dict[str, Any]] = []
    for idx, pkg in enumerate(pkgs, start=1):
        pid = str(pkg.get("id") or f"pkg_{idx}")
        evidence = pkg.get("evidence") if isinstance(pkg.get("evidence"), Mapping) else {}
        sizing = pkg.get("sizing") if isinstance(pkg.get("sizing"), Mapping) else {}
        entry = pkg.get("entry") if isinstance(pkg.get("entry"), Mapping) else {}
        exit_p = pkg.get("exit") if isinstance(pkg.get("exit"), Mapping) else {}
        margin = float(sizing.get("margin_usd") or 0.25)
        leverage = float(sizing.get("leverage") or 100)
        notional = margin * leverage
        tiles.append({
            "lane": f"STRATEGY_PACKAGE::{pid}",
            "label": str(pkg.get("name") or pid),
            "subtitle": "STRATEGY PACKAGE — REGISTERED — NOT ACTIVATED (needs own R22 + RK PASS)",
            "status": "NOT_ACTIVATED",
            "badge": "STRATEGY_PACKAGE · NOT_ACTIVATED",
            "strategy_package": True,
            "strategy_package_id": pid,
            "strategy_package_activated": False,
            "paper_only": True,
            "relay_eligible": False,
            "planned": False,
            "tile_number": None,
            "registry_position": None,
            "package_registry_position": idx,
            "toggle_key": None,
            "default_enabled": False,
            "hypothesis": str(pkg.get("thesis") or ""),
            "series_stamp": str(pkg.get("series_stamp") or ""),
            "kind": str(pkg.get("kind") or ""),
            "evidence_status": evidence.get("status"),
            "feeds_r53c": feeds_r53c_gate(pkg),
            "filter_chips": [
                f"kind={pkg.get('kind')}",
                f"evidence={evidence.get('status')}",
                f"costs={pkg.get('costs')}",
                "paper_only",
                "NOT_ACTIVATED",
            ],
            "entry": {
                "signal": entry.get("signal"),
                "params": dict(entry.get("params") or {}),
                "cooldown_min": entry.get("cooldown_min"),
                "margin_usd": margin,
                "leverage": leverage,
                "notional_usd": notional,
                "cadence_label": f"package signal {entry.get('signal') or '—'}",
                "chase_detail": "not activated — no paper orders",
            },
            "exit": {
                "composite": list(exit_p.get("composite") or []),
                "thesis_cut": exit_p.get("thesis_cut"),
                "hard_stop_pct": exit_p.get("hard_stop_pct"),
                "tp": exit_p.get("tp"),
                "max_hold_min": exit_p.get("max_hold_min"),
                "profile": "package_exit_not_activated",
            },
            "card_sections": {
                "schema": "strategy_package_card_v1",
                "entry": [
                    f"Signal: {entry.get('signal') or 'not declared'}",
                    f"Cooldown: {entry.get('cooldown_min')} min",
                    f"Size: ${margin:g} margin @{leverage:g}x ~ ${notional:g} notional (never max loss)",
                ],
                "exit": [
                    f"Composite: {' → '.join(str(x) for x in (exit_p.get('composite') or []))}",
                    f"Hard stop: {exit_p.get('hard_stop_pct')}%",
                    f"Max hold: {exit_p.get('max_hold_min')} min",
                ],
                "risk": [
                    "Paper only — not activated",
                    f"Daily loss cap: ${pkg.get('daily_loss_cap_usd')}",
                    f"R53(c) eligible: {'yes' if feeds_r53c_gate(pkg) else 'no (need FWD_PASS)'}",
                ],
            },
            "session_stats": {
                "approves": 0,
                "real_fills": 0,
                "net_pnl_real": 0.0,
                "scope": "STRATEGY_PACKAGE_NOT_ACTIVATED",
            },
        })
    return tiles
