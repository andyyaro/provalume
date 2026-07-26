"""Call-capture shim for trajectory benchmarking.

Put this file's directory on PYTHONPATH and set PROVALUME_TRACE_OUT to a
writable file path. Every public method call on
`provalume.integrations.orkestra.OrkestraAdapter` — the exact surface
Orkestra's `memory.py` drives — is appended to that file as one JSON line,
with kwargs and (for reads) the returned value.

The shim must never change the run it observes: every failure path is
swallowed, and when PROVALUME_TRACE_OUT is unset or provalume is not
importable it does nothing at all.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time

_TRUNCATED = "…[truncated]"


def _capped(text: str, limit: int) -> str:
    # Every lossy path marks itself, so the loader can refuse an unfaithful
    # fixture instead of replaying different bytes than the run saw.
    return text if len(text) <= limit else text[:limit] + _TRUNCATED


def _encode(obj: object, depth: int = 0) -> object:
    if depth > 6:
        return _capped(repr(obj), 500)
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        return _capped(obj, 20000)
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_encode(x, depth + 1) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _encode(v, depth + 1) for k, v in obj.items()}
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {
            "__dataclass__": type(obj).__name__,
            **{
                f.name: _encode(getattr(obj, f.name), depth + 1)
                for f in dataclasses.fields(obj)
            },
        }
    d = getattr(obj, "__dict__", None)
    if isinstance(d, dict) and d:
        return {
            "__class__": type(obj).__name__,
            **{
                k: _encode(v, depth + 1)
                for k, v in d.items()
                if not k.startswith("_")
            },
        }
    return _capped(repr(obj), 2000)


def _install() -> None:
    out = os.environ.get("PROVALUME_TRACE_OUT")
    if not out:
        return
    try:
        import provalume.integrations.orkestra as m
    except Exception:
        return

    reads = {"brief_digest", "preflight"}

    def write(record: dict) -> None:
        try:
            line = json.dumps(
                record, ensure_ascii=False, default=lambda x: _capped(repr(x), 500)
            )
            with open(out, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def wrap(name: str, orig):
        def patched(self, *args, **kwargs):
            record = {
                "ts": time.time(),
                "pid": os.getpid(),
                "method": name,
                "args": _encode(args),
                "kwargs": _encode(kwargs),
            }
            try:
                result = orig(self, *args, **kwargs)
            except BaseException as exc:
                record["error"] = f"{type(exc).__name__}: {exc}"
                write(record)
                raise
            if name in reads:
                record["result"] = _encode(result)
            write(record)
            return result

        patched.__name__ = name
        patched.__qualname__ = f"OrkestraAdapter.{name}"
        return patched

    cls = m.OrkestraAdapter
    for name, attr in list(vars(cls).items()):
        if name.startswith("_") or not callable(attr):
            continue
        setattr(cls, name, wrap(name, attr))

    orig_init = cls.__init__

    def patched_init(self, provalume, context):
        orig_init(self, provalume, context)
        write(
            {
                "ts": time.time(),
                "pid": os.getpid(),
                "method": "__init__",
                "context": _encode(context),
            }
        )

    cls.__init__ = patched_init


_install()
