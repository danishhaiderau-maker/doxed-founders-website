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
- OOS_PASS / FWD_PASS without 22m label HOLDS or with leak_suspect != false
- any ``strategies/*.yaml`` while numeric-bars follow-up is still required

Missing PyYAML / jsonschema → soft fallback: **no package tiles** (dashboard
stays up). CI installs both via ``requirements.txt``.

R53(c): only ``evidence.status == FWD_PASS`` may feed the R53(c) gate helper.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)

try:
    import yaml as _yaml
except ImportError:  # pragma: no cover - exercised via deps_available tests
    _yaml = None

try:
    import jsonschema as _jsonschema
except ImportError:  # pragma: no cover
    _jsonschema = None


SCHEMA_NAME = "strategy_package_v1"
EVIDENCE_STATUSES = frozenset({"LEAD", "OOS_PASS", "FWD_PASS"})
R53C_ELIGIBLE_STATUS = "FWD_PASS"
HOLDS_LABEL = "HOLDS"

# Numeric bars (HOLDS PF/trades/quarters/slippage, R53(c) folds, kill rule,
# REALISTIC_V1 drift) are documented in strategies/NUMERIC-BARS-FOLLOWUP.md and
# gate ANY strategies/*.yaml until implemented + RK PASS. Flip only after that.
NUMERIC_BARS_FOLLOWUP_REQUIRED = True

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


def deps_available() -> bool:
    """True when PyYAML and jsonschema both import."""
    return _yaml is not None and _jsonschema is not None


def missing_deps() -> tuple[str, ...]:
    missing: list[str] = []
    if _yaml is None:
        missing.append("PyYAML")
    if _jsonschema is None:
        missing.append("jsonschema")
    return tuple(missing)


def schema_path() -> Path:
    return DEFAULT_SCHEMA_PATH


def load_schema(path: Path | None = None) -> dict[str, Any]:
    if _jsonschema is None:
        raise StrategyPackageRefuse(
            "jsonschema not installed — pip install -r services/btc-conservative-agent/requirements.txt"
        )
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


def _label_is_holds(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().upper() == HOLDS_LABEL
    if isinstance(value, Mapping):
        for key in ("label", "status", "verdict", "result"):
            if str(value.get(key) or "").strip().upper() == HOLDS_LABEL:
                return True
    return False


def oos_22m_holds(evidence: Mapping[str, Any]) -> bool:
    """True when the 22m OOS label is HOLDS (``oos_22m`` or legacy ``oos_22m_summary``)."""
    for key in ("oos_22m", "oos_22m_summary"):
        if key in evidence and _label_is_holds(evidence.get(key)):
            return True
    return False


def validate_package_dict(
    raw: Mapping[str, Any],
    *,
    source: str = "<dict>",
    schema: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate + hard-refuse. Returns a plain dict copy on success."""
    if not deps_available():
        raise StrategyPackageRefuse(
            f"{source}: missing deps {missing_deps()} — "
            "pip install -r services/btc-conservative-agent/requirements.txt"
        )

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

    if status in ("OOS_PASS", "FWD_PASS"):
        if not oos_22m_holds(evidence):
            raise StrategyPackageRefuse(
                f"{source}: evidence.status={status} requires 22m label HOLDS "
                f"(set evidence.oos_22m or oos_22m_summary to {HOLDS_LABEL!r})"
            )
        if evidence.get("leak_suspect") is not False:
            raise StrategyPackageRefuse(
                f"{source}: evidence.status={status} requires leak_suspect: false "
                f"(got {evidence.get('leak_suspect')!r})"
            )

    sch = schema if schema is not None else load_schema()
    try:
        _jsonschema.validate(instance=data, schema=sch)
    except _jsonschema.ValidationError as exc:
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
    if evidence.get("status") != R53C_ELIGIBLE_STATUS:
        return False
    # FWD_PASS already required HOLDS + leak_suspect false at validate time;
    # re-check so a hand-built dict cannot sneak through.
    if not oos_22m_holds(evidence):
        return False
    if evidence.get("leak_suspect") is not False:
        return False
    return True


def assert_r53c_eligible(package: Mapping[str, Any], *, source: str = "<package>") -> None:
    if not feeds_r53c_gate(package):
        status = None
        evidence = package.get("evidence") if isinstance(package, Mapping) else None
        if isinstance(evidence, Mapping):
            status = evidence.get("status")
        raise StrategyPackageRefuse(
            f"{source}: R53(c) gate refuses evidence.status={status!r}; "
            f"only {R53C_ELIGIBLE_STATUS} with HOLDS + leak_suspect false may feed R53(c)"
        )


def load_package_file(
    path: Path | str,
    *,
    schema: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if _yaml is None:
        raise StrategyPackageRefuse(
            f"{path}: PyYAML not installed — "
            "pip install -r services/btc-conservative-agent/requirements.txt"
        )
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise StrategyPackageRefuse(f"{p}: cannot read ({exc})") from exc
    try:
        raw = _yaml.safe_load(text)
    except _yaml.YAMLError as exc:
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


def assert_numeric_bars_gate(
    strategies_dir: Path | str | None = None,
    *,
    enforce: bool | None = None,
) -> None:
    """Refuse any registered strategies/*.yaml while numeric-bars follow-up is open."""
    do_enforce = NUMERIC_BARS_FOLLOWUP_REQUIRED if enforce is None else bool(enforce)
    if not do_enforce:
        return
    paths = iter_strategy_yaml_paths(strategies_dir)
    if not paths:
        return
    raise StrategyPackageRefuse(
        "numeric bars follow-up required before any strategies/*.yaml "
        "(HOLDS: PF≥1.10, ≥200 trades, ≥60% quarters positive, net>0 at 1.5× slippage; "
        "R53(c): ≥200 OOS trades, ≥70% monthly folds positive; per-tile kill; "
        "paper-fill vs REALISTIC_V1 drift). "
        "See strategies/NUMERIC-BARS-FOLLOWUP.md. Files: "
        + ", ".join(p.name for p in paths)
    )


def load_registered_packages(
    strategies_dir: Path | str | None = None,
    *,
    schema: Mapping[str, Any] | None = None,
    enforce_numeric_bars_gate: bool | None = None,
) -> list[dict[str, Any]]:
    """Load every ``strategies/<id>.yaml``. Fail closed on the first refuse.

    When the directory is empty, schema is **not** loaded (no YAML → skip).
    Missing PyYAML/jsonschema → empty list (dashboard soft-fallback).
    """
    paths = iter_strategy_yaml_paths(strategies_dir)
    if not paths:
        return []
    if not deps_available():
        logger.warning(
            "[STRATEGY_PACKAGE] deps missing %s — returning no package tiles",
            missing_deps(),
        )
        return []
    assert_numeric_bars_gate(strategies_dir, enforce=enforce_numeric_bars_gate)
    sch = schema if schema is not None else load_schema()
    packages: list[dict[str, Any]] = []
    for path in paths:
        packages.append(load_package_file(path, schema=sch))
    return packages


def build_dashboard_tile_specs(
    packages: list[Mapping[str, Any]] | None = None,
    *,
    strategies_dir: Path | str | None = None,
    enforce_numeric_bars_gate: bool | None = None,
) -> list[dict[str, Any]]:
    """One dashboard tile card per registered package (never activated).

    Soft-fallback: missing deps or empty dir → ``[]`` (no schema load).
    """
    if packages is None:
        if not deps_available():
            logger.warning(
                "[STRATEGY_PACKAGE] deps missing %s — no package tiles",
                missing_deps(),
            )
            return []
        try:
            pkgs = load_registered_packages(
                strategies_dir,
                enforce_numeric_bars_gate=enforce_numeric_bars_gate,
            )
        except Exception as exc:  # noqa: BLE001 — dashboard must not crash (incl. refuse)
            logger.error("[STRATEGY_PACKAGE] load failed: %s", exc)
            return []
    else:
        pkgs = list(packages)

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
                    f"R53(c) eligible: {'yes' if feeds_r53c_gate(pkg) else 'no (need FWD_PASS+HOLDS)'}",
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
