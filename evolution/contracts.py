"""Validate only bundled schemas; do not fetch external schema references."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[1]


def validate_contract(name, value):
    bundle = json.loads((ROOT / "protocol/loop_a_contracts.schema.json").read_text(encoding="utf-8"))
    if name not in bundle["$defs"]:
        raise ValueError(f"Unknown contract: {name}")
    schema = {"$schema": bundle["$schema"], "$ref": f"#/$defs/{name}", "$defs": bundle["$defs"]}
    Draft202012Validator.check_schema(schema)
    try:
        Draft202012Validator(schema).validate(value)
    except ValidationError as exc:
        # Report only schema locations; values may contain private task content.
        location = "/".join(str(part) for part in exc.absolute_path) or "<root>"
        raise ValueError(f"Invalid {name} contract at {location}: {exc.validator}") from None
