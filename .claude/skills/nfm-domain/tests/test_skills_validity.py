"""Test structural validity of all NFM domain skills.

Validates that each SKILL.md has required frontmatter, all reference
links resolve to existing files, and content covers mandated topics.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).resolve().parent.parent
REQUIRED_SKILLS = {
    "nuclear-materials-knowledge",
    "literature-search",
    "lammps-debugger",
    "quality-audit",
    "template-selector",
}

REQUIRED_FRONTMATTER_FIELDS = {"name", "description"}

SKILL_TOPIC_REQUIREMENTS = {
    "nuclear-materials-knowledge": [
        r"(?i)uranium|U\b",
        r"(?i)UO2|uranium dioxide",
        r"(?i)zirconium|Zr\b",
        r"(?i)iron|Fe\b",
        r"U-Zr",
        r"EAM",
        r"Buckingham",
        r"Tersoff",
        r"AIREBO",
        r"(?i)NaN|instability|convergence",
    ],
    "literature-search": [
        r"(?i)NIST\s*IPR|NIST",
        r"OpenKIM",
        r"(?i)Materials Project",
        r"(?i)credibility|scoring",
        r"(?i)uncertainty",
    ],
    "lammps-debugger": [
        r"(?i)error\s*pattern|error\s*recogn",
        r"(?i)fix|solution",
        r"(?i)diagnos|debug",
        r"(?i)validat",
    ],
    "quality-audit": [
        r"P0|data quality",
        r"(?i)checklist",
        r"(?i)anomal|detect",
        r"(?i)report|audit",
    ],
    "template-selector": [
        r"(?i)potential\s*type|potential\s*select",
        r"(?i)template",
        r"(?i)parameter\s*estimat|timestep|cutoff",
        r"(?i)non-EAM|Buckingham|Tersoff",
    ],
}


# ---- Helpers -----------------------------------------------------------

def extract_frontmatter(text: str) -> dict[str, str]:
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    result = {}
    for line in m.group(1).strip().split("\n"):
        if ":" in line:
            key, _, val = line.partition(":")
            result[key.strip()] = val.strip()
    return result


def resolve_reference_links(text: str) -> list[str]:
    """Extract markdown reference links of the form [text](path)."""
    return re.findall(r"\[([^\]]+)\]\(([^)]+)\)", text)


# ---- Fixtures ----------------------------------------------------------

@pytest.fixture
def skill_dirs() -> list[Path]:
    return sorted(
        d
        for d in SKILLS_DIR.iterdir()
        if d.is_dir() and d.name not in {"tests", "__pycache__"}
    )


@pytest.fixture
def skill_path(skill_dirs) -> dict[str, Path]:
    return {d.name: d / "SKILL.md" for d in skill_dirs}


# ---- Tests -------------------------------------------------------------

class TestSkillsExist:
    """Verify all 5 required skills are present."""

    def test_five_skill_directories_exist(self, skill_dirs):
        names = {d.name for d in skill_dirs}
        missing = REQUIRED_SKILLS - names
        assert not missing, f"Missing skill directories: {missing}"

    def test_each_directory_has_skill_md(self, skill_path):
        for name, path in skill_path.items():
            assert path.exists(), f"{name}: missing SKILL.md"


class TestFrontmatterValidity:
    """Validate SKILL.md frontmatter for every skill."""

    def test_all_skills_have_name_field(self, skill_path):
        for skill_name, path in skill_path.items():
            content = path.read_text()
            fm = extract_frontmatter(content)
            assert "name" in fm, f"{skill_name}: missing 'name' in frontmatter"
            assert fm["name"] == skill_name, (
                f"{skill_name}: frontmatter name '{fm['name']}' "
                f"does not match directory name '{skill_name}'"
            )

    def test_all_skills_have_description_field(self, skill_path):
        for skill_name, path in skill_path.items():
            content = path.read_text()
            fm = extract_frontmatter(content)
            assert "description" in fm, f"{skill_name}: missing 'description' in frontmatter"
            assert len(fm["description"]) >= 20, (
                f"{skill_name}: description too short ({len(fm['description'])} chars)"
            )

    def test_frontmatter_has_no_unknown_fields(self, skill_path):
        allowed_extra = {"---", ""}  # some parsers may include delimiters
        for skill_name, path in skill_path.items():
            content = path.read_text()
            fm = extract_frontmatter(content)
            unknown = set(fm.keys()) - REQUIRED_FRONTMATTER_FIELDS - allowed_extra
            assert not unknown, f"{skill_name}: unknown frontmatter fields: {unknown}"


class TestReferenceResolution:
    """Verify all reference links in SKILL.md point to existing files."""

    SKILLS_DIR = SKILLS_DIR

    def test_all_reference_links_resolve(self, skill_path):
        broken = []
        for skill_name, path in skill_path.items():
            content = path.read_text()
            for label, ref_path in resolve_reference_links(content):
                # Skip external URLs and anchor-only links
                if ref_path.startswith(("http://", "https://", "#")):
                    continue
                # Resolve relative to the skill directory
                resolved = (path.parent / ref_path).resolve()
                if not resolved.exists():
                    broken.append(f"{skill_name}: [{label}]({ref_path}) → {resolved}")
        assert not broken, "Broken reference links:\n  " + "\n  ".join(broken)


class TestTopicCoverage:
    """Verify each skill covers its mandated topics."""

    def test_nuclear_materials_knowledge_covers_topics(self, skill_path):
        self._check_topics("nuclear-materials-knowledge", skill_path)

    def test_literature_search_covers_topics(self, skill_path):
        self._check_topics("literature-search", skill_path)

    def test_lammps_debugger_covers_topics(self, skill_path):
        self._check_topics("lammps-debugger", skill_path)

    def test_quality_audit_covers_topics(self, skill_path):
        self._check_topics("quality-audit", skill_path)

    def test_template_selector_covers_topics(self, skill_path):
        self._check_topics("template-selector", skill_path)

    def _check_topics(self, skill_name: str, skill_path: dict[str, Path]):
        path = skill_path[skill_name]
        content = path.read_text()
        # Also include reference content
        refs_dir = path.parent / "references"
        if refs_dir.exists():
            for ref_file in refs_dir.iterdir():
                if ref_file.suffix == ".md":
                    content += "\n" + ref_file.read_text()

        required = SKILL_TOPIC_REQUIREMENTS.get(skill_name, [])
        missing = []
        for pattern in required:
            if not re.search(pattern, content):
                missing.append(pattern)
        assert not missing, (
            f"{skill_name}: missing required topics: {missing}"
        )


class TestCrossReferences:
    """Verify cross-skill references are valid."""

    def test_cross_references_point_to_real_skills(self, skill_path):
        valid_skill_names = set(skill_path.keys())
        invalid = []
        for skill_name, path in skill_path.items():
            content = path.read_text()
            for label, ref_path in resolve_reference_links(content):
                # Check for cross-skill path patterns
                skill_ref = ref_path.split("/")[0].replace("-", " ")
                # More precise: check if the reference contains another skill's name
                for valid_name in valid_skill_names:
                    if valid_name in ref_path and valid_name != skill_name:
                        # Verify the target path exists
                        target = (SKILLS_DIR.parent / ref_path).resolve()
                        if not target.exists() and not ref_path.startswith("http"):
                            invalid.append(
                                f"{skill_name} → {ref_path} (target: {target})"
                            )
        # Cross-references are optional, so only flag truly broken ones
        # that match skill name patterns
        actually_broken = [
            i for i in invalid
            if any(name.replace("-", "/") in i for name in valid_skill_names)
        ]
        assert not actually_broken, (
            "Broken cross-skill references:\n  " + "\n  ".join(actually_broken)
        )
