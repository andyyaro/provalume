"""Post-state probes over a captured scenario's real memory database.

Usage: probe.py <project_dir> <project_id> <query1> [query2 ...]
Prints JSON observations used to author benchmark expectations.
"""

import json
import sys
import tomllib
from pathlib import Path

from provalume import Provalume
from provalume.integrations.orkestra import OrkestraAdapter, OrkestraContext

project = Path(sys.argv[1])
project_id = sys.argv[2]
queries = sys.argv[3:]

config = tomllib.loads((project / ".orkestra" / "config.toml").read_text())
commands = config["verify"]["commands"]

client = Provalume.open(
    project / ".orkestra" / "memory.db", project_id=project_id, root=project
)
adapter = OrkestraAdapter(client, OrkestraContext(project_id=project_id))

out: dict = {"project_id": project_id}

out["preflight"] = []
for command in commands:
    r = adapter.preflight(command=command, record=False)
    out["preflight"].append(
        {
            "command": command[-60:],
            "matched": r.matched,
            "matches": [
                {
                    "occurrences": m.occurrences,
                    "confidence": m.confidence,
                    "what_later_worked": m.what_later_worked,
                    "failure_signature": getattr(m, "failure_signature", ""),
                    "memory_type": str(getattr(m, "memory_type", "")),
                }
                for m in r.matches
            ],
        }
    )

out["digests"] = []
for q in queries:
    d = adapter.brief_digest(query=q, char_budget=2000)
    out["digests"].append(
        {
            "query": q,
            "items": len(d.items),
            "chars_used": d.chars_used,
            "text_head": d.text[:400],
        }
    )

out["recall"] = []
for q in queries:
    rr = client.recall(q, limit=5)
    out["recall"].append(
        {
            "query": q,
            "n": len(rr),
            "top": [
                {
                    "type": str(x.memory_type),
                    "trust": str(x.trust_state),
                    "text": x.text[:100],
                    "current_truth": x.presentable_as_current_truth,
                }
                for x in rr
            ],
        }
    )

records = client.memory_records()
out["memories"] = [
    {
        "type": str(m.memory_type),
        "trust": str(m.trust_state),
        "text": m.text[:110],
    }
    for m in records
]

events = client.events()
by_type: dict[str, int] = {}
for e in events:
    t = str(e.event_type)
    by_type[t] = by_type.get(t, 0) + 1
out["events_by_type"] = dict(sorted(by_type.items()))

sample = [e for e in events if "verification" in str(e.event_type)][:2]
out["verification_excerpt_sample"] = [
    str((e.payload or {}).get("excerpt", ""))[:300] for e in sample
]

client.close()
print(json.dumps(out, indent=1, default=str))
