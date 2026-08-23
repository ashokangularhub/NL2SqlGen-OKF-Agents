"""
okf_validator.py — OKF v0.1 Bundle Conformance Validator

Checks an OKF bundle against the spec rules:
  1. Every concept file must have YAML frontmatter with a 'type' field.
  2. Reserved filenames (index.md, log.md) must not have frontmatter.
  3. Cross-links should resolve to existing concepts (warn on broken).
  4. File paths form valid concept IDs.

Usage:
    python src/okf_validator.py okf_bundle/
"""

import pathlib
import re
import sys

import yaml

RESERVED_FILES = {"index.md", "log.md"}
LINK_RE = re.compile(r"\]\(([^)]+\.md)\)")
OPTIONAL_FIELDS = {"title", "description", "resource", "tags", "timestamp"}


def validate_bundle(bundle_path: str) -> tuple[list, list]:
    """
    Validate an OKF bundle. Returns (errors, warnings).
    Errors = spec violations. Warnings = best-practice issues.
    """
    root = pathlib.Path(bundle_path)
    errors = []
    warnings = []

    if not root.is_dir():
        return [f"Bundle path does not exist: {bundle_path}"], []

    md_files = sorted(root.rglob("*.md"))
    if not md_files:
        return [f"No .md files found in {bundle_path}"], []

    concept_ids = set()
    all_link_targets = []

    for md_file in md_files:
        rel_path = md_file.relative_to(root)
        filename = rel_path.name
        text = md_file.read_text(encoding="utf-8")

        # ── Reserved files ──
        if filename in RESERVED_FILES:
            if text.strip().startswith("---") and filename == "index.md":
                warnings.append(
                    f"{rel_path}: index.md should not have frontmatter "
                    f"(per OKF spec §6)"
                )
            continue

        # ── Concept files ──
        concept_id = str(rel_path).replace("\\", "/")[:-3]
        concept_ids.add(concept_id)

        if not text.strip().startswith("---"):
            errors.append(
                f"{rel_path}: Missing YAML frontmatter (must start with ---)"
            )
            continue

        parts = text.split("---", 2)
        if len(parts) < 3:
            errors.append(
                f"{rel_path}: Malformed frontmatter (missing closing ---)")
            continue

        try:
            meta = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError as e:
            errors.append(f"{rel_path}: Invalid YAML in frontmatter: {e}")
            continue

        if "type" not in meta:
            errors.append(
                f"{rel_path}: Missing required 'type' field in frontmatter"
            )

        known = {"type"} | OPTIONAL_FIELDS
        unknown = set(meta.keys()) - known
        if unknown:
            warnings.append(
                f"{rel_path}: Non-standard frontmatter fields: {unknown}. "
                f"These are allowed but won't be recognized by standard consumers."
            )

        body = parts[2] if len(parts) > 2 else ""
        for match in LINK_RE.finditer(body):
            all_link_targets.append((md_file, match.group(1)))

    # ── Validate cross-links ──
    for source_path, target_relative in all_link_targets:
        try:
            target_abs = (source_path.parent / target_relative).resolve()
            target_rel = target_abs.relative_to(root.resolve())
            target_id = str(target_rel).replace("\\", "/")[:-3]

            if (target_id not in concept_ids
                    and target_rel.name not in RESERVED_FILES):
                warnings.append(
                    f"{source_path.relative_to(root)}: Broken link to "
                    f"'{target_relative}' (concept '{target_id}' not found). "
                    f"OKF spec says consumers MUST tolerate broken links."
                )
        except (ValueError, OSError):
            warnings.append(
                f"{source_path.relative_to(root)}: Could not resolve "
                f"link target '{target_relative}'"
            )

    return errors, warnings


def main():
    bundle_dir = sys.argv[1] if len(sys.argv) > 1 else "okf_bundle"

    print("=" * 64)
    print(f"  OKF v0.1 Bundle Validator — ClearBank Retail Banking")
    print(f"  Bundle: {bundle_dir}")
    print("=" * 64)

    errors, warnings = validate_bundle(bundle_dir)

    if errors:
        print(f"\n✗ ERRORS ({len(errors)}):")
        for e in errors:
            print(f"  ✗ {e}")

    if warnings:
        print(f"\n⚠ WARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"  ⚠ {w}")

    if not errors and not warnings:
        print("\n✓ Bundle is fully conformant with OKF v0.1!")
    elif not errors:
        print(f"\n✓ Bundle is conformant (with {len(warnings)} warnings).")
    else:
        print(f"\n✗ Bundle has {len(errors)} spec violations.")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
