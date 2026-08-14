#!/usr/bin/env python3
"""
Build sast_rules.json (Piece 2 - Semantic Memory M_s) for AgenticSCR.

Source: github/codeql -> python/ql/src/Security/CWE-*/*.ql (+ .qhelp examples)

Each rule record:
{
  "id": "py/sql-injection",
  "name": "SQL query built from user-controlled sources",
  "description": "...",
  "cwe_ids": ["CWE-089"],
  "severity": "error",
  "security_severity": 8.8,
  "precision": "high",
  "tags": ["security", "external/cwe/cwe-089"],
  "language": "python",
  "kind": "path-problem",
  "source_query_path": "python/ql/src/Security/CWE-089/SqlInjection.ql",
  "examples": {
     "bad": ["<code snippet>", ...],
     "good": ["<code snippet>", ...]
  }
}
"""
import json
import re
from pathlib import Path

REPO_ROOT = (_REPO_ROOT / "codeql")
SECURITY_DIR = REPO_ROOT / "python/ql/src/Security"
OUT_PATH = (_REPO_ROOT / "sast_rules/sast_rules.json")

QLDOC_BLOCK_RE = re.compile(r"/\*\*(.*?)\*/", re.DOTALL)
FIELD_RE = re.compile(r"@(\w[\w.-]*)\s+(.*?)(?=(?:\n@)|\Z)", re.DOTALL)


def parse_qldoc(ql_text: str) -> dict:
    m = QLDOC_BLOCK_RE.search(ql_text)
    if not m:
        return {}
    block = m.group(1)
    # strip leading " * " from each line
    lines = [re.sub(r"^\s*\*\s?", "", l) for l in block.splitlines()]
    cleaned = "\n".join(lines)
    fields = {}
    for fm in FIELD_RE.finditer(cleaned + "\n@__end__ "):
        key = fm.group(1).strip()
        val = " ".join(fm.group(2).split())
        if key == "__end__":
            continue
        if key in fields:
            # tags can repeat implicitly via multi-line; keep as list
            if isinstance(fields[key], list):
                fields[key].append(val)
            else:
                fields[key] = [fields[key], val]
        else:
            fields[key] = val
    return fields


def extract_cwe_ids(tags_field) -> list:
    if tags_field is None:
        return []
    if isinstance(tags_field, str):
        tags_field = [tags_field]
    text = " ".join(tags_field)
    ids = re.findall(r"cwe-(\d+)", text, re.IGNORECASE)
    return [f"CWE-{i.zfill(3)}" for i in ids]


def split_tags(tags_field) -> list:
    if tags_field is None:
        return []
    if isinstance(tags_field, str):
        tags_field = [tags_field]
    tags = []
    for chunk in tags_field:
        tags.extend(chunk.split())
    return tags


def extract_examples_from_py(py_path: Path) -> dict:
    """Pull contiguous snippets around '# BAD' and '# GOOD' markers."""
    bad, good = [], []
    try:
        text = py_path.read_text(errors="ignore")
    except Exception:
        return {"bad": [], "good": []}

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        marker = None
        if re.search(r"#\s*BAD", line, re.IGNORECASE):
            marker = "bad"
        elif re.search(r"#\s*GOOD", line, re.IGNORECASE):
            marker = "good"
        if marker:
            snippet = [line]
            j = i + 1
            # collect until next blank-ish boundary or next marker/def
            while j < len(lines):
                nxt = lines[j]
                if re.search(r"#\s*(BAD|GOOD)", nxt, re.IGNORECASE):
                    break
                if nxt.strip() == "" and len(snippet) > 1:
                    break
                snippet.append(nxt)
                j += 1
            code = "\n".join(snippet).strip()
            if marker == "bad":
                bad.append(code)
            else:
                good.append(code)
            i = j
        else:
            i += 1
    return {"bad": bad, "good": good}


def main():
    rules = []
    ql_files = sorted(SECURITY_DIR.glob("*/*.ql"))
    for ql_path in ql_files:
        text = ql_path.read_text(errors="ignore")
        fields = parse_qldoc(text)
        if not fields.get("id"):
            continue  # skip non-query helper files

        cwe_folder = ql_path.parent.name  # e.g. CWE-089
        cwe_ids = extract_cwe_ids(fields.get("tags")) or (
            [cwe_folder] if cwe_folder.startswith("CWE-") else []
        )

        examples_dir = ql_path.parent / "examples"
        examples = {"bad": [], "good": []}
        if examples_dir.is_dir():
            for py_file in sorted(examples_dir.glob("*.py")):
                ex = extract_examples_from_py(py_file)
                examples["bad"].extend(ex["bad"])
                examples["good"].extend(ex["good"])

        sev = fields.get("security-severity")
        try:
            sev = float(sev) if sev else None
        except ValueError:
            sev = None

        rule = {
            "id": fields.get("id"),
            "name": fields.get("name", "").strip(),
            "description": fields.get("description", "").strip(),
            "cwe_ids": cwe_ids,
            "severity": fields.get("problem.severity"),
            "security_severity": sev,
            "precision": fields.get("precision"),
            "tags": split_tags(fields.get("tags")),
            "language": "python",
            "kind": fields.get("kind"),
            "source_query_path": str(ql_path.relative_to(REPO_ROOT)),
            "examples": examples,
        }
        rules.append(rule)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(rules, indent=2))
    print(f"Wrote {len(rules)} rules to {OUT_PATH}")

    # quick stats
    with_examples = sum(1 for r in rules if r["examples"]["bad"] or r["examples"]["good"])
    print(f"Rules with extracted examples: {with_examples}/{len(rules)}")
    unique_cwes = sorted({c for r in rules for c in r["cwe_ids"]})
    print(f"Unique CWEs covered: {len(unique_cwes)} -> {unique_cwes}")


if __name__ == "__main__":
    main()
