"""Strictly bounded JSON projection for snapshots held under trading locks."""
import math


class ProjectionUnavailable(ValueError):
    pass


def project_fields(source, keys, *, node_limit=4096, field_limit=512, depth_limit=8, text_limit=4096):
    if type(source) is not dict:
        return {}
    remaining = [node_limit]
    unavailable = {}
    def clone(value, depth, budget):
        if remaining[0] <= 0 or budget[0] <= 0:
            raise ProjectionUnavailable("NODE_LIMIT")
        remaining[0] -= 1
        budget[0] -= 1
        kind = type(value)
        if value is None or kind in (bool, int):
            return value
        if kind is float:
            if not math.isfinite(value):
                raise ProjectionUnavailable("NONFINITE_NUMBER")
            return value
        if kind is str:
            if len(value) > text_limit:
                raise ProjectionUnavailable("TEXT_LIMIT")
            return value
        if depth >= depth_limit:
            raise ProjectionUnavailable("DEPTH_LIMIT")
        if kind not in (dict, list, tuple):
            raise ProjectionUnavailable("NON_JSON_TYPE")
        if len(value) > min(remaining[0], budget[0]):
            raise ProjectionUnavailable("CONTAINER_LIMIT")
        if kind is dict:
            result = {}
            for key, item in value.items():
                if type(key) is not str or len(key) > text_limit:
                    raise ProjectionUnavailable("KEY_TYPE_OR_SIZE")
                result[key] = clone(item, depth + 1, budget)
            return result
        return [clone(item, depth + 1, budget) for item in value]
    result = {}
    for key in sorted(keys):
        if key not in source:
            continue
        try:
            result[key] = clone(source[key], 0, [field_limit])
        except (ProjectionUnavailable, RuntimeError, KeyError) as exc:
            result[key] = None
            unavailable[key] = str(exc) if type(exc) is ProjectionUnavailable else "CONCURRENT_MUTATION"
    if unavailable:
        result["dashboard_projection"] = {"status": "PARTIAL", "unavailable_fields": unavailable}
        if "research_chase_schedule" in unavailable:
            result["chase_schedule_authoritative"] = False
    return result
