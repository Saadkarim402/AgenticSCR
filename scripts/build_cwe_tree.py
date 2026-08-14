#!/usr/bin/env python3
"""
Build cwe_tree.json (Piece 3 - Semantic Memory M_s, used by Validator Subagent)

Source: local `cwe` PyPI package (v1.6), which bundles MITRE's official
CWE CSV export as a pickled dict (839 entries) -- no network access needed
since cwe.mitre.org isn't reachable from this sandbox.

Output structure per entry:
{
  "cwe_id": "CWE-089",
  "name": "...",
  "abstraction": "Base|Class|Variant|Pillar|Compound",
  "description": "...",
  "extended_description": "...",
  "category": "Injection" (derived from Pillar/Class ancestor when available),
  "parents": ["CWE-943", ...]       # ChildOf, View 1000, primary or not
  "children": ["CWE-90", ...],       # inverse of parents within View 1000
  "detection_methods": ["..."],
  "potential_mitigations": ["..."],
  "common_consequences": ["..."],
  "modes_of_introduction": ["..."],
  "observed_examples": ["..."],
  "likelihood_of_exploit": "...",
}

Also writes a `roots` list (Pillar-level CWEs with no parent in View 1000)
so the tree can be walked top-down.
"""
import gzip
import importlib.util
import json
import os
import pickle
import re
from pathlib import Path

OUT_PATH = (_REPO_ROOT / "cwe_tree/cwe_tree.json")


def load_raw_db() -> dict:
    spec = importlib.util.find_spec("cwe")
    base = os.path.dirname(spec.origin)
    with gzip.open(os.path.join(base, "db.pickle.gz"), "rb") as f:
        return pickle.loads(f.read())


def parse_related_weaknesses(raw: str, view_filter: str = "1000"):
    """
    Format looks like:
    ::NATURE:ChildOf:CWE ID:642:VIEW ID:1000:ORDINAL:Primary::NATURE:ChildOf:CWE ID:610:VIEW ID:1000::
    Returns list of dicts: {nature, cwe_id, view_id, ordinal}
    """
    if not raw:
        return []
    entries = []
    for chunk in raw.split("::"):
        chunk = chunk.strip()
        if not chunk or "NATURE" not in chunk:
            continue
        fields = {}
        parts = chunk.split(":")
        # parts like ['NATURE','ChildOf','CWE ID','642','VIEW ID','1000','ORDINAL','Primary']
        it = iter(parts)
        keyvals = list(zip(*[iter(parts)] * 1))  # no-op, keep parts
        i = 0
        while i < len(parts) - 1:
            key = parts[i]
            val = parts[i + 1]
            if key in ("NATURE", "CWE ID", "VIEW ID", "ORDINAL"):
                fields[key] = val
                i += 2
            else:
                i += 1
        entries.append(fields)

    out = []
    for e in entries:
        if view_filter and e.get("VIEW ID") != view_filter:
            continue
        cid = e.get("CWE ID")
        if not cid:
            continue
        out.append(
            {
                "nature": e.get("NATURE"),
                "cwe_id": f"CWE-{cid.zfill(3)}",
                "ordinal": e.get("ORDINAL", ""),
            }
        )
    return out


def split_pipe_field(raw: str, item_key: str = "DESCRIPTION"):
    """Generic parser for '::PHASE:...:DESCRIPTION:...::PHASE:...::' style fields.
    Falls back to returning the raw text as a single-item list if no DESCRIPTION markers found.
    """
    if not raw:
        return []
    if "DESCRIPTION" not in raw and "NOTE" not in raw:
        # Some fields (Observed Examples) use REFERENCE/DESCRIPTION differently; just split on ::
        parts = [p.strip() for p in raw.split("::") if p.strip()]
        return parts
    results = []
    for chunk in raw.split("::"):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.search(r"(?:DESCRIPTION|NOTE):(.*)$", chunk, re.DOTALL)
        if m:
            text = m.group(1).strip()
            if text:
                results.append(text)
    return results


def main():
    raw_db = load_raw_db()

    nodes = {}
    for k, entry in raw_db.items():
        cwe_id = f"CWE-{str(entry.get('CWE-ID', k)).zfill(3)}"
        related = parse_related_weaknesses(entry.get("Related Weaknesses", ""))
        parents = [r["cwe_id"] for r in related if r["nature"] == "ChildOf"]

        nodes[cwe_id] = {
            "cwe_id": cwe_id,
            "name": entry.get("Name", "").strip(),
            "abstraction": entry.get("Weakness Abstraction", "").strip(),
            "status": entry.get("Status", "").strip(),
            "description": entry.get("Description", "").strip(),
            "extended_description": entry.get("Extended Description", "").strip(),
            "parents": parents,
            "children": [],  # filled below
            "detection_methods": split_pipe_field(entry.get("Detection Methods", "")),
            "potential_mitigations": split_pipe_field(entry.get("Potential Mitigations", "")),
            "common_consequences": split_pipe_field(entry.get("Common Consequences", "")),
            "modes_of_introduction": split_pipe_field(entry.get("Modes Of Introduction", "")),
            "observed_examples": split_pipe_field(entry.get("Observed Examples", "")),
            "likelihood_of_exploit": entry.get("Likelihood of Exploit", "").strip(),
        }

    # build children (inverse of parents), only within our node set
    for cwe_id, node in nodes.items():
        for p in node["parents"]:
            if p in nodes:
                nodes[p]["children"].append(cwe_id)

    roots = sorted(
        [cid for cid, n in nodes.items() if not n["parents"] and n["abstraction"] == "Pillar"]
    )
    # fallback roots: any node with zero parents at all (in case some Pillars mis-tagged)
    if not roots:
        roots = sorted([cid for cid, n in nodes.items() if not n["parents"]])

    tree = {
        "view_id": "1000",
        "view_name": "CWE-1000: Research Concepts",
        "source": "MITRE CWE List (via local `cwe` PyPI package v1.6, offline pickle export)",
        "total_entries": len(nodes),
        "roots": roots,
        "entries": nodes,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(tree, indent=2))
    print(f"Wrote {len(nodes)} CWE entries to {OUT_PATH}")
    print(f"Pillar roots found: {len(roots)} -> {roots}")
    with_parents = sum(1 for n in nodes.values() if n["parents"])
    print(f"Entries with >=1 parent in View 1000: {with_parents}/{len(nodes)}")


if __name__ == "__main__":
    main()
