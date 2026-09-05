"""Small original-exception receipts independent of application log handlers."""
import json
import os


MAX_RECEIPT_BYTES = 4096
MAX_TRACEBACK_FRAMES = 16


def emit_original_exception_receipt(exc_type, exc_value, exc_traceback):
    """Best-effort stderr write with bounded content, not an IO deadline.

    Deliberately omit exception messages, locals, source lines, and full paths.
    Call before application logging so a failing log sink cannot hide the cause.
    """
    try:
        frames = []
        current = exc_traceback
        # Bound traversal as well as serialized output; never inspect locals.
        while current is not None and len(frames) < MAX_TRACEBACK_FRAMES:
            code = current.tb_frame.f_code
            frames.append({
                "file": str(code.co_filename).replace("\\", "/").rsplit("/", 1)[-1][:64],
                "function": str(code.co_name)[:64],
                "line": int(current.tb_lineno),
            })
            current = current.tb_next
        errno = getattr(exc_value, "errno", None) if isinstance(exc_value, OSError) else None
        receipt = {
            "schema": "original_exception_receipt_v1",
            "exception_type": str(getattr(exc_type, "__name__", "UnknownException"))[:96],
            "errno": errno if isinstance(errno, int) and not isinstance(errno, bool) else None,
            "frames": frames,
            "frames_truncated": current is not None,
        }
        payload = (json.dumps(receipt, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")
        # Escaping unusual names may exceed the cap. Retain valid JSON, not a
        # truncated byte string, and preserve exception identity before frames.
        while len(payload) > MAX_RECEIPT_BYTES and receipt["frames"]:
            receipt["frames"].pop()
            receipt["frames_truncated"] = True
            payload = (json.dumps(receipt, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(payload) <= MAX_RECEIPT_BYTES:
            os.write(2, payload)
    except Exception:
        # Diagnostic failures must not replace the original exception.
        pass
