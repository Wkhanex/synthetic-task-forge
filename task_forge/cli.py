import argparse
import json
import sys
from .core import PackageError, build, check_package, read_json, validate_manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build and validate reproducible synthetic evaluation packages.")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("manifest")
    create = sub.add_parser("build")
    create.add_argument("manifest")
    create.add_argument("--output", required=True)
    check = sub.add_parser("check")
    check.add_argument("package")
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            data = validate_manifest(read_json(args.manifest))
            result = {"valid": True, "task_id": data["task_id"]}
        elif args.command == "build":
            result = build(read_json(args.manifest), args.output)
        else:
            result = {"checks": check_package(args.package)}
        print(json.dumps(result, indent=2))
        return 1 if args.command == "check" and any(not r["passed"] for r in result["checks"]) else 0
    except (PackageError, OSError, ValueError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
