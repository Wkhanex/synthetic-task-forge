"""Manifest validation, exact invariants and deterministic package construction."""
import csv
from decimal import Decimal, localcontext
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tempfile
import zipfile


class PackageError(ValueError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def filename(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", value):
        raise PackageError(f"invalid flat filename: {value!r}")
    if value.casefold() in {"prompt.md", "manifest.json", "checksums.json", "check-results.json"} or value.lower().endswith(".zip"):
        raise PackageError(f"reserved filename: {value}")
    return value


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"),
                          parse_constant=lambda v: (_ for _ in ()).throw(PackageError(f"nonfinite JSON value: {v}")))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise PackageError(f"invalid JSON: {path}: {exc}") from exc


def validate_manifest(data):
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise PackageError("manifest schema_version must be 1")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", str(data.get("task_id", ""))):
        raise PackageError("task_id must be a lowercase slug")
    if not isinstance(data.get("prompt"), str) or not data["prompt"].strip():
        raise PackageError("prompt must be nonempty text")
    files = data.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= 50:
        raise PackageError("files must contain between 1 and 50 artifacts")
    names = set()
    for artifact in files:
        if not isinstance(artifact, dict):
            raise PackageError("each artifact must be an object")
        name = filename(artifact.get("name"))
        if name.casefold() in names:
            raise PackageError(f"duplicate case-insensitive filename: {name}")
        names.add(name.casefold())
        if artifact.get("audience") not in {"solver", "reviewer"}:
            raise PackageError(f"{name}: audience must be solver or reviewer")
        if artifact.get("format") not in {"csv", "json", "text"}:
            raise PackageError(f"{name}: unsupported format")
        if artifact["format"] == "csv":
            fields, rows = artifact.get("columns"), artifact.get("rows")
            if not isinstance(fields, list) or not fields or not all(isinstance(c, str) and c for c in fields):
                raise PackageError(f"{name}: columns must be nonempty strings")
            if len(fields) != len(set(fields)) or not isinstance(rows, list):
                raise PackageError(f"{name}: duplicate columns or invalid rows")
            for row in rows:
                if not isinstance(row, dict) or set(row) != set(fields):
                    raise PackageError(f"{name}: CSV row keys must equal columns")
                if any(not isinstance(v, (str, int, bool)) and v is not None for v in row.values()):
                    raise PackageError(f"{name}: CSV values must be strings, integers, booleans or null")
        elif artifact["format"] == "text" and not isinstance(artifact.get("content"), str):
            raise PackageError(f"{name}: text content must be a string")
        elif artifact["format"] == "json" and "content" not in artifact:
            raise PackageError(f"{name}: JSON content is required")
    if not 1 <= sum(f["audience"] == "solver" for f in files) <= 20:
        raise PackageError("between 1 and 20 solver artifacts are required")
    invariants = data.get("invariants", [])
    if not isinstance(invariants, list):
        raise PackageError("invariants must be a list")
    allowed = {"csv_sum", "csv_unique", "csv_foreign_key", "json_equals", "text_contains"}
    ids = set()
    for rule in invariants:
        if not isinstance(rule, dict) or not isinstance(rule.get("id"), str) or not rule["id"]:
            raise PackageError("invariants require nonempty IDs")
        if rule["id"] in ids or rule.get("kind") not in allowed:
            raise PackageError("duplicate invariant ID or unsupported invariant kind")
        ids.add(rule["id"])
        if rule.get("file") not in {f["name"] for f in files}:
            raise PackageError(f"{rule['id']}: file is not declared")
        if rule["kind"] == "csv_foreign_key" and rule.get("target") not in {f["name"] for f in files}:
            raise PackageError(f"{rule['id']}: target is not declared")
    terms = data.get("forbidden_solver_terms", [])
    if not isinstance(terms, list) or not all(isinstance(t, str) and t for t in terms):
        raise PackageError("forbidden_solver_terms must be nonempty strings")
    # Refuse nonfinite/unserializable JSON anywhere, including reviewer content.
    canonical(data)
    return data


def render(artifact):
    if artifact["format"] == "json":
        return canonical(artifact["content"])
    if artifact["format"] == "text":
        return artifact["content"].encode("utf-8")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=artifact["columns"], lineterminator="\n")
    writer.writeheader()
    writer.writerows(artifact["rows"])
    return stream.getvalue().encode("utf-8")


def _decimal(value):
    if isinstance(value, bool) or not re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", str(value)):
        raise PackageError(f"invalid exact decimal: {value!r}")
    number = Decimal(str(value))
    if len(number.as_tuple().digits) > 100 or abs(number.adjusted()) > 100:
        raise PackageError("numeric invariant exceeds supported precision")
    return number


def _csv(blob, column):
    reader = csv.DictReader(io.StringIO(blob.decode("utf-8"), newline=""), strict=True)
    if column not in (reader.fieldnames or []):
        raise PackageError(f"missing CSV column: {column}")
    rows = list(reader)
    if any(None in r or any(v is None for v in r.values()) for r in rows):
        raise PackageError("malformed CSV row")
    return rows


def pointer(document, path):
    if path == "":
        return document
    if not isinstance(path, str) or not path.startswith("/"):
        raise PackageError("JSON pointer must be empty or start with /")
    value = document
    for token in path[1:].split("/"):
        if re.search(r"~(?![01])", token):
            raise PackageError("invalid JSON pointer escape")
        token = token.replace("~1", "/").replace("~0", "~")
        try:
            value = value[int(token)] if isinstance(value, list) and token.isdecimal() else value[token]
        except (KeyError, IndexError, TypeError) as exc:
            raise PackageError(f"JSON pointer not found: {path}") from exc
    return value


def evaluate(manifest, blobs):
    results = []
    for rule in manifest.get("invariants", []):
        try:
            blob, kind = blobs[rule["file"]], rule["kind"]
            if kind == "csv_sum":
                with localcontext() as context:
                    context.prec = 220
                    actual = sum((_decimal(r[rule["column"]]) for r in _csv(blob, rule["column"])), Decimal(0))
                    expected = _decimal(rule["expected"])
                    passed, detail = actual == expected, f"sum {actual}; expected {expected}"
            elif kind == "csv_unique":
                values = [r[rule["column"]] for r in _csv(blob, rule["column"])]
                passed = len(values) == len(set(values)) and all(values)
                detail = "nonempty unique values required"
            elif kind == "csv_foreign_key":
                values = [r[rule["column"]] for r in _csv(blob, rule["column"])]
                targets = {r[rule["target_column"]] for r in _csv(blobs[rule["target"]], rule["target_column"])}
                missing = sorted(set(values) - targets)
                passed, detail = not missing, f"missing target keys: {missing}"
            elif kind == "json_equals":
                actual = pointer(json.loads(blob), rule.get("pointer", ""))
                expected = rule["expected"]
                passed = canonical(actual) == canonical(expected)
                detail = f"value {actual!r}; expected {expected!r}"
            else:
                passed = rule["text"] in blob.decode("utf-8")
                detail = "required text must occur verbatim"
            results.append({"id": rule["id"], "passed": passed, "detail": detail})
        except (PackageError, KeyError, UnicodeError, ValueError, TypeError, csv.Error) as exc:
            results.append({"id": rule["id"], "passed": False, "detail": str(exc)})
    for artifact in manifest["files"]:
        if artifact["audience"] != "solver":
            continue
        text = blobs[artifact["name"]].decode("utf-8").casefold()
        leaked = [term for term in manifest.get("forbidden_solver_terms", []) if term.casefold() in text]
        results.append({"id": f"audience:{artifact['name']}", "passed": not leaked, "detail": f"forbidden terms: {leaked}"})
    prompt_leaks = [term for term in manifest.get("forbidden_solver_terms", []) if term.casefold() in manifest["prompt"].casefold()]
    results.append({"id": "audience:prompt", "passed": not prompt_leaks, "detail": f"forbidden terms: {prompt_leaks}"})
    return results


def deterministic_zip(path, members):
    with Path(path).open("xb") as stream, zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(members.items()):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)


def build(manifest, output):
    validate_manifest(manifest)
    output = Path(output)
    if output.exists():
        raise PackageError("output already exists; choose a new directory")
    blobs = {a["name"]: render(a) for a in manifest["files"]}
    checks = evaluate(manifest, blobs)
    if any(not r["passed"] for r in checks):
        raise PackageError("package checks failed: " + ", ".join(r["id"] for r in checks if not r["passed"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".forge-") as temp:
        root = Path(temp) / "package"
        (root / "inputs").mkdir(parents=True)
        (root / "reviewer").mkdir()
        checksums = {}
        members = {"prompt.md": manifest["prompt"].encode("utf-8")}
        for artifact in manifest["files"]:
            relative = ("inputs/" if artifact["audience"] == "solver" else "reviewer/") + artifact["name"]
            (root / relative).write_bytes(blobs[artifact["name"]])
            checksums[relative] = hashlib.sha256(blobs[artifact["name"]]).hexdigest()
            if artifact["audience"] == "solver":
                members[artifact["name"]] = blobs[artifact["name"]]
        (root / "prompt.md").write_bytes(members["prompt.md"])
        checksums["prompt.md"] = hashlib.sha256(members["prompt.md"]).hexdigest()
        (root / "reviewer/manifest.json").write_bytes(canonical(manifest))
        (root / "reviewer/checksums.json").write_bytes(canonical(checksums))
        (root / "reviewer/check-results.json").write_bytes(canonical(checks))
        deterministic_zip(root / "input_files.zip", members)
        root.rename(output)
    return {"task_id": manifest["task_id"], "solver_files": len(members) - 1, "checks": len(checks), "output": str(output)}


def check_package(root):
    root = Path(root).resolve()
    for relative in ("reviewer/manifest.json", "reviewer/checksums.json", "input_files.zip"):
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise PackageError("control file is not contained within the package")
    manifest = validate_manifest(read_json(root / "reviewer/manifest.json"))
    blobs, checks = {}, []
    for artifact in manifest["files"]:
        relative = ("inputs/" if artifact["audience"] == "solver" else "reviewer/") + artifact["name"]
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise PackageError("artifact path is not contained within the package")
        try:
            blobs[artifact["name"]] = path.read_bytes()
        except OSError:
            blobs[artifact["name"]] = b""
        checks.append({"id": f"content:{artifact['name']}", "passed": path.is_file() and blobs[artifact["name"]] == render(artifact), "detail": "content must match the manifest"})
    checks.extend(evaluate(manifest, blobs))
    sums = read_json(root / "reviewer/checksums.json")
    expected_paths = {("inputs/" if a["audience"] == "solver" else "reviewer/") + a["name"] for a in manifest["files"]} | {"prompt.md"}
    if not isinstance(sums, dict):
        raise PackageError("checksums must be an object")
    checks.append({"id": "hash:inventory", "passed": set(sums) == expected_paths, "detail": "hash inventory must cover every artifact and prompt"})
    for name, digest in sums.items():
        parts = PurePosixPath(name)
        target = root.joinpath(*parts.parts)
        if parts.is_absolute() or ".." in parts.parts or target.is_symlink() or not target.resolve().is_relative_to(root):
            raise PackageError("unsafe checksum path")
        valid = target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digest
        checks.append({"id": f"hash:{name}", "passed": valid, "detail": "stored SHA-256 must match current bytes"})
    expected = {"prompt.md": manifest["prompt"].encode(), **{a["name"]: blobs[a["name"]] for a in manifest["files"] if a["audience"] == "solver"}}
    try:
        with zipfile.ZipFile(root / "input_files.zip") as archive:
            valid = len(archive.namelist()) == len(expected) and set(archive.namelist()) == set(expected)
            valid = valid and all(archive.read(name) == content for name, content in expected.items())
    except (OSError, KeyError, zipfile.BadZipFile):
        valid = False
    checks.append({"id": "delivery:zip", "passed": valid, "detail": "delivery members must equal solver artifacts and prompt"})
    return checks
