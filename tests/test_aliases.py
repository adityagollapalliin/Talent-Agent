from skillpulse.config import ROOT, load_settings
from skillpulse.extract.aliases import Aliases

S = load_settings(ROOT / "config.yaml")
A = Aliases.load(ROOT / "memory" / "skill_aliases.yaml", S.focus_list())


def test_spec_examples():
    for alias in ("GCP", "Google Cloud Provider", "Google Cloud", "gcp", "Google  Cloud."):
        assert A.canonical(alias) == "Google Cloud Platform"
    assert A.canonical("k8s") == "Kubernetes"
    assert A.canonical("Claude") == "Anthropic / Claude"
    assert A.canonical("GenAI") == "Generative AI"
    assert A.canonical("Generative AI") == "Generative AI"


def test_unknown_skill_kept_as_is():
    assert A.canonical("  Elixir ") == "Elixir"


def test_normalise_list_dedupes_by_canonical():
    assert A.normalise_list(["AWS", "Amazon Web Services", "aws", "k8s", "Kubernetes"]) == ["AWS", "Kubernetes"]


def test_focus_names_are_canonical_without_alias_entry():
    assert A.canonical("rag") == "RAG"
    assert A.canonical("vector databases") == "Vector databases"


def test_focus_hits_use_groups():
    hits = A.focus_hits(["Pinecone", "EKS", "Jenkins", "PySpark", "Python"])
    assert set(hits) == {"Vector databases", "AWS", "Kubernetes", "CI/CD", "Spark / PySpark", "Python"}
    assert A.focus_hits(["Elixir"]) == []


def test_certification_aliases():
    assert A.canonical("AI-102") == "Microsoft Azure AI Engineer Associate (AI-102)"
    assert A.canonical("AWS Certified Solutions Architect - Associate") == "AWS Certified Solutions Architect – Associate"
