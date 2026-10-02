import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from complete_spec_package import detect_knowledge_docs


def test_detect_knowledge_docs_matches_underscored_slug():
    with tempfile.TemporaryDirectory() as tmpdir:
        docs_dir = Path(tmpdir)
        slug = "2026-05-01_rigor-review"
        (docs_dir / f"2026-05-01_{slug}_经验.md").touch()
        (docs_dir / f"2026-05-01_{slug}_路线图.md").touch()

        result = detect_knowledge_docs(docs_dir, slug)
        assert len(result) == 2
        assert all(slug in r for r in result)


def test_detect_knowledge_docs_no_match():
    with tempfile.TemporaryDirectory() as tmpdir:
        docs_dir = Path(tmpdir)
        result = detect_knowledge_docs(docs_dir, "nonexistent-slug")
        assert result == []
