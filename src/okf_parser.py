"""
okf_parser.py — OKF Bundle Parser & Knowledge Graph Builder

Supports TWO loading strategies:

  1. load_bundle()         — Eager: reads ALL concept files upfront.
                             Simple, good for small bundles (< 100 concepts).

  2. BundleNavigator       — Lazy / Progressive Disclosure:
                             reads index.md first, then loads only the
                             sections and concepts the agent actually needs.
                             Google's recommended approach for large bundles.

Usage:
    python src/okf_parser.py okf_bundle/
"""

import pathlib
import re
import sys
from dataclasses import dataclass, field

import yaml


# ── Data Models ──────────────────────────────────────────────────────

RESERVED_FILES = {"index.md", "log.md"}
LINK_RE = re.compile(r"\]\(([^)]+\.md)\)")


@dataclass
class Concept:
    """A single OKF concept parsed from a markdown file."""
    concept_id: str
    file_path: str
    concept_type: str
    title: str = ""
    description: str = ""
    resource: str = ""
    tags: list = field(default_factory=list)
    timestamp: str = ""
    body: str = ""
    outgoing_links: list = field(default_factory=list)


@dataclass
class IndexEntry:
    """One item parsed from an index.md listing."""
    title: str
    relative_path: str
    description: str
    section: str


# ── Low-Level Helpers ────────────────────────────────────────────────

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from markdown body."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = yaml.safe_load(parts[1]) or {}
    body = parts[2].strip()
    return meta, body


def resolve_link(source_path: pathlib.Path, relative_target: str,
                 bundle_root: pathlib.Path) -> str | None:
    """Resolve a relative markdown link to a concept ID."""
    try:
        target_path = (source_path.parent / relative_target).resolve()
        rel = target_path.relative_to(bundle_root.resolve())
        concept_id = str(rel).replace("\\", "/")
        if concept_id.endswith(".md"):
            concept_id = concept_id[:-3]
        return concept_id
    except (ValueError, OSError):
        return None


def parse_concept_file(md_file: pathlib.Path,
                       bundle_root: pathlib.Path) -> Concept:
    """Parse a single concept .md file into a Concept object."""
    rel_path = md_file.relative_to(bundle_root)
    concept_id = str(rel_path).replace("\\", "/")[:-3]

    text = md_file.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)

    concept = Concept(
        concept_id=concept_id,
        file_path=str(rel_path),
        concept_type=meta.get("type", "Unknown"),
        title=meta.get("title", concept_id.split("/")[-1]),
        description=meta.get("description", ""),
        resource=meta.get("resource", ""),
        tags=meta.get("tags", []),
        timestamp=str(meta.get("timestamp", "")),
        body=body,
    )

    for match in LINK_RE.finditer(body):
        target_id = resolve_link(md_file, match.group(1), bundle_root)
        if target_id and target_id != concept_id:
            concept.outgoing_links.append(target_id)

    return concept


def parse_index_file(index_path: pathlib.Path) -> list[IndexEntry]:
    """Parse an index.md into structured IndexEntry objects."""
    if not index_path.exists():
        return []

    text = index_path.read_text(encoding="utf-8")
    entries = []

    heading_re = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)
    item_re = re.compile(
        r"^\*\s+\[([^\]]+)\]\(([^)]+)\)"
        r"(?:\s*[-–—]\s*(.+))?$",
        re.MULTILINE,
    )

    headings = [(m.start(), m.group(1).strip())
                for m in heading_re.finditer(text)]

    for match in item_re.finditer(text):
        pos = match.start()
        section = "Root"
        for h_pos, h_title in headings:
            if h_pos < pos:
                section = h_title
            else:
                break

        entries.append(IndexEntry(
            title=match.group(1).strip(),
            relative_path=match.group(2).strip(),
            description=(match.group(3) or "").strip(),
            section=section,
        ))

    return entries


# ═════════════════════════════════════════════════════════════════════
# Strategy 1: Eager Full Load
# ═════════════════════════════════════════════════════════════════════

@dataclass
class KnowledgeBundle:
    """Eagerly-loaded OKF bundle with full knowledge graph."""
    root: str
    concepts: dict = field(default_factory=dict)
    edges: list = field(default_factory=list)

    @property
    def concept_count(self):
        return len(self.concepts)

    def get_by_type(self, concept_type: str) -> list:
        return [c for c in self.concepts.values()
                if c.concept_type.lower() == concept_type.lower()]

    def get_by_tag(self, tag: str) -> list:
        return [c for c in self.concepts.values() if tag in c.tags]


def load_bundle(bundle_path: str) -> KnowledgeBundle:
    """EAGER LOAD — reads every .md file in the bundle."""
    root = pathlib.Path(bundle_path)
    if not root.is_dir():
        raise FileNotFoundError(f"Bundle path not found: {bundle_path}")

    bundle = KnowledgeBundle(root=str(root))

    for md_file in sorted(root.rglob("*.md")):
        if md_file.name in RESERVED_FILES:
            continue
        concept = parse_concept_file(md_file, root)
        bundle.concepts[concept.concept_id] = concept

    for concept in bundle.concepts.values():
        for target_id in concept.outgoing_links:
            if target_id in bundle.concepts:
                bundle.edges.append((concept.concept_id, target_id))

    return bundle


# ═════════════════════════════════════════════════════════════════════
# Strategy 2: Progressive Disclosure (Google's recommended approach)
# ═════════════════════════════════════════════════════════════════════

class BundleNavigator:
    """
    LAZY / PROGRESSIVE DISCLOSURE — reads files on demand.

    Flow:
      1. __init__()        → reads ONLY root index.md
      2. list_sections()   → returns available sections
      3. load_section()    → reads all concepts in ONE subdirectory
      4. load_concept()    → reads a SINGLE concept by ID
      5. follow_links()    → follows cross-links from loaded concepts
    """

    def __init__(self, bundle_path: str):
        self.root = pathlib.Path(bundle_path).resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f"Bundle not found: {bundle_path}")

        self._files_read = 0
        self._total_files = sum(
            1 for f in self.root.rglob("*.md")
            if f.name not in RESERVED_FILES
        )
        self._loaded_concepts: dict[str, Concept] = {}
        self._root_index: list[IndexEntry] = []
        self._sections: dict[str, list[IndexEntry]] = {}

        self._root_index = parse_index_file(self.root / "index.md")
        for entry in self._root_index:
            self._sections.setdefault(entry.section, []).append(entry)

    def list_sections(self) -> dict[str, list[str]]:
        return {
            section: [e.title for e in entries]
            for section, entries in self._sections.items()
        }

    def get_section_summary(self) -> str:
        lines = ["Available knowledge sections:\n"]
        for section, entries in self._sections.items():
            lines.append(f"  [{section}]")
            for entry in entries:
                desc = f" — {entry.description}" if entry.description else ""
                lines.append(f"    • {entry.title}{desc}")
        return "\n".join(lines)

    def load_section(self, section_name: str) -> list[Concept]:
        entries = self._sections.get(section_name, [])
        if not entries:
            for key, val in self._sections.items():
                if key.lower() == section_name.lower():
                    entries = val
                    break

        loaded = []
        for entry in entries:
            concept_path = (self.root / entry.relative_path).resolve()
            if concept_path.exists() and concept_path.suffix == ".md":
                concept = self._load_file(concept_path)
                if concept:
                    loaded.append(concept)
        return loaded

    def load_concept(self, concept_id: str) -> Concept | None:
        if concept_id in self._loaded_concepts:
            return self._loaded_concepts[concept_id]
        md_path = self.root / f"{concept_id}.md"
        if md_path.exists():
            return self._load_file(md_path)
        return None

    def follow_links(self, concepts: list[Concept],
                     max_hops: int = 1,
                     max_links: int = 5) -> list[Concept]:
        newly_loaded = []
        frontier = list(concepts)

        for _ in range(max_hops):
            next_frontier = []
            for concept in frontier:
                for link_id in concept.outgoing_links:
                    if len(newly_loaded) >= max_links:
                        return newly_loaded
                    if link_id not in self._loaded_concepts:
                        linked = self.load_concept(link_id)
                        if linked:
                            newly_loaded.append(linked)
                            next_frontier.append(linked)
            frontier = next_frontier

        return newly_loaded

    def _load_file(self, md_path: pathlib.Path) -> Concept | None:
        try:
            concept = parse_concept_file(md_path, self.root)
            if concept.concept_id not in self._loaded_concepts:
                self._files_read += 1
            self._loaded_concepts[concept.concept_id] = concept
            return concept
        except Exception as e:
            print(f"  ⚠ Failed to load {md_path}: {e}")
            return None

    @property
    def files_read(self) -> int:
        return self._files_read

    @property
    def total_files(self) -> int:
        return self._total_files

    @property
    def loaded_concepts(self) -> dict[str, Concept]:
        return dict(self._loaded_concepts)

    def get_stats(self) -> dict:
        pct = round(100.0 * self._files_read / self._total_files,
                    1) if self._total_files else 0
        return {
            "files_read": self._files_read,
            "total_files": self._total_files,
            "pct_loaded": pct,
        }


# ═════════════════════════════════════════════════════════════════════
# CLI — run parser directly to inspect the bundle
# ═════════════════════════════════════════════════════════════════════

def main():
    bundle_dir = sys.argv[1] if len(sys.argv) > 1 else "okf_bundle"
    print(f"\n{'='*60}")
    print(f"  OKF Bundle Parser — ClearBank Retail Banking")
    print(f"  Bundle: {bundle_dir}")
    print(f"{'='*60}\n")

    # Eager load
    bundle = load_bundle(bundle_dir)
    print(
        f"Loaded {bundle.concept_count} concepts, {len(bundle.edges)} edges\n")

    for ctype in ["Dataset", "Table", "Metric", "Runbook"]:
        concepts = bundle.get_by_type(ctype)
        if concepts:
            print(f"  {ctype}s ({len(concepts)}):")
            for c in concepts:
                print(f"    • {c.title} [{c.concept_id}]")
                if c.outgoing_links:
                    print(f"      → links: {', '.join(c.outgoing_links[:3])}")

    print(f"\n  Knowledge Graph Edges ({len(bundle.edges)}):")
    for src, tgt in bundle.edges[:10]:
        print(f"    {src}  →  {tgt}")

    # Progressive navigator demo
    print(f"\n{'─'*60}")
    print("  Progressive Disclosure Demo")
    print(f"{'─'*60}")
    nav = BundleNavigator(bundle_dir)
    print(f"  After init (only index.md read):")
    print(f"  Sections: {list(nav.list_sections().keys())}")
    tables = nav.load_section("Tables")
    print(
        f"\n  After load_section('Tables'): {nav.files_read}/{nav.total_files} files read")
    linked = nav.follow_links(tables, max_hops=1, max_links=4)
    print(
        f"  After follow_links: {nav.files_read}/{nav.total_files} files read")
    stats = nav.get_stats()
    print(f"  Efficiency: {stats['pct_loaded']}% of bundle loaded")


if __name__ == "__main__":
    main()
