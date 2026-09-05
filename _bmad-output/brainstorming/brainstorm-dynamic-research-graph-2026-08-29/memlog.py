from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def atomic_write(path: Path, content: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def log_path(workspace: str) -> Path:
    path = Path(workspace) / ".memlog.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--workspace", required=True)
    init.add_argument("--field", action="append", default=[])
    append = commands.add_parser("append")
    append.add_argument("--workspace", required=True)
    append.add_argument("--type", dest="kind")
    append.add_argument("--text", required=True)
    append.add_argument("--by")
    append_many = commands.add_parser("append-many")
    append_many.add_argument("--workspace", required=True)
    append_many.add_argument("--input", required=True)
    set_value = commands.add_parser("set")
    set_value.add_argument("--workspace", required=True)
    set_value.add_argument("--key", required=True)
    set_value.add_argument("--value", required=True)
    args = parser.parse_args()
    path = log_path(args.workspace)

    if args.command == "init":
        if path.exists():
            raise SystemExit(f"memlog already exists: {path}")
        fields = {
            "status": "active",
            "created_at": datetime.now(UTC).isoformat(),
        }
        for item in args.field:
            key, value = item.split("=", 1)
            fields[key] = value
        frontmatter = "".join(
            f"{key}: {json.dumps(value, ensure_ascii=False)}\n"
            for key, value in fields.items()
        )
        atomic_write(path, f"---\n{frontmatter}---\n\n# Session log\n")
    elif args.command == "append":
        entries = [{"kind": args.kind, "text": args.text, "by": args.by}]
        for entry in entries:
            existing = path.read_text(encoding="utf-8")
            if not existing.startswith("---\n"):
                raise SystemExit(f"invalid memlog: {path}")
            timestamp = datetime.now(UTC).isoformat()
            label = entry["kind"] or "note"
            by = f" by {entry['by']}" if entry["by"] else ""
            text = " ".join(entry["text"].splitlines()).strip()
            atomic_write(path, f"{existing}- [{timestamp}] ({label}{by}) {text}\n")
    elif args.command == "append-many":
        entries = json.loads(Path(args.input).read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            raise SystemExit("append-many input must be a JSON list")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
                raise SystemExit("each append-many entry requires text")
            existing = path.read_text(encoding="utf-8")
            if not existing.startswith("---\n"):
                raise SystemExit(f"invalid memlog: {path}")
            timestamp = datetime.now(UTC).isoformat()
            label = entry.get("kind") or "note"
            by = f" by {entry['by']}" if entry.get("by") else ""
            text = " ".join(entry["text"].splitlines()).strip()
            atomic_write(path, f"{existing}- [{timestamp}] ({label}{by}) {text}\n")
    else:
        existing = path.read_text(encoding="utf-8")
        pattern = rf"(?m)^{re.escape(args.key)}: .*?$"
        if not re.search(pattern, existing):
            raise SystemExit(f"missing frontmatter key: {args.key}")
        updated = re.sub(
            pattern,
            f"{args.key}: {json.dumps(args.value, ensure_ascii=False)}",
            existing,
            count=1,
        )
        atomic_write(path, updated)


if __name__ == "__main__":
    main()
