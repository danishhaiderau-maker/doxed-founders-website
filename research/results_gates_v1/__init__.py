"""Read-only Stage C results consumer.

Synthetic fixture evaluation only. Real gate profiles, producer pins and
research conclusions are not established by importing this package.
"""

from .gates import PROFILE_ID, evaluate_fixture, profile_sha256, synthetic_profile
from .guards import BOUNDS
from .validate import EVALUATION_STATUS, RESULTS_SCHEMA, RESULTS_VERSION, UNKNOWNS

CARD_SHA256 = "c0b239a5b6f8b076d1d4e36535c4a4055d21d9838da18a7cafcb5b1059f3b81b"
CARD_BYTES = 9566
DESIGN_SPEC_SHA256 = {
    "stage_a": "ecb42d094510f7dd507b4ebc5fb2797a2614951614745d1fcfbaa79e44bfb122",
    "stage_b": "025082ee2c98c9d8e8637e6ea1d23f446546b2fc935ecf8119533ccbef2e9f22",
}

__all__ = (
    "BOUNDS",
    "CARD_BYTES",
    "CARD_SHA256",
    "DESIGN_SPEC_SHA256",
    "EVALUATION_STATUS",
    "PROFILE_ID",
    "RESULTS_SCHEMA",
    "RESULTS_VERSION",
    "UNKNOWNS",
    "evaluate_fixture",
    "profile_sha256",
    "synthetic_profile",
)
