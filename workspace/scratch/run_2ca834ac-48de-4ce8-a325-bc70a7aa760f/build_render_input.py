import json, pathlib

root = pathlib.Path(r"c:\Users\wushuhong\Desktop\literature-research-clean-e2e-20260810-195219\workspace")
md = (root / "report.md").read_text(encoding="utf-8")

citations = [
    {"citation_id": "lkv_exp", "paper_ref": "paper_50caf2c8-6053-435a-b7f3-126ca2ec3769", "locator": {"kind": "section", "value": "Experiments"}},
    {"citation_id": "vqkv_exp", "paper_ref": "paper_f93ee025-14bd-4d0d-bf97-8bc19caaacfb", "locator": {"kind": "section", "value": "Experiment"}},
    {"citation_id": "cake_exp", "paper_ref": "paper_4e8f6440-1612-4d3e-8db8-f6448a37a481", "locator": {"kind": "section", "value": "Experimentation"}},
    {"citation_id": "rocketkv_method", "paper_ref": "paper_73ca1a25-eebf-4359-af53-ef40a6855748", "locator": {"kind": "section", "value": "Proposed Method: RocketKV"}},
    {"citation_id": "rocketkv_exp", "paper_ref": "paper_73ca1a25-eebf-4359-af53-ef40a6855748", "locator": {"kind": "section", "value": "Experiments"}},
    {"citation_id": "quantspec_bottleneck", "paper_ref": "paper_7e0d8e58-d37d-4835-8a6b-c0600f7fc397", "locator": {"kind": "section", "value": "LLM Inference Bottlenecks"}},
    {"citation_id": "kqsvd_method", "paper_ref": "paper_3551a2c1-4ca1-45a9-b0db-6aa3f1a4f922", "locator": {"kind": "section", "value": "Methodology"}},
    {"citation_id": "kqsvd_exp", "paper_ref": "paper_3551a2c1-4ca1-45a9-b0db-6aa3f1a4f922", "locator": {"kind": "section", "value": "Experiments"}},
    {"citation_id": "nsa_exp", "paper_ref": "paper_fa5091d7-cac6-4903-9ac5-0503228a0dc8", "locator": {"kind": "section", "value": "Experiments"}},
    {"citation_id": "nha_exp", "paper_ref": "paper_8be2bb1a-cdb4-4b2c-b9dc-4b554916dba0", "locator": {"kind": "section", "value": "Experiments"}},
    {"citation_id": "kvlink_method", "paper_ref": "paper_0d3c3604-20ad-40fd-bb00-a547e1e69e50", "locator": {"kind": "section", "value": "Methodology"}},
    {"citation_id": "kvlink_exp", "paper_ref": "paper_0d3c3604-20ad-40fd-bb00-a547e1e69e50", "locator": {"kind": "section", "value": "Experiments"}},
    {"citation_id": "lmcache_overview", "paper_ref": "paper_b4920f81-b210-4a71-9646-35e3b2691c4e", "locator": {"kind": "section", "value": "Overview of LMCACHE"}},
    {"citation_id": "lmcache_eval", "paper_ref": "paper_b4920f81-b210-4a71-9646-35e3b2691c4e", "locator": {"kind": "section", "value": "Evaluation"}},
]

# Verify every token in the markdown has a citation
import re
tokens = set(re.findall(r"\{\{cite:([a-z_0-9]+)\}\}", md))
cids = {c["citation_id"] for c in citations}
missing = tokens - cids
extra = cids - tokens
print("TOKENS IN MD:", sorted(tokens))
print("CITATION IDS:", sorted(cids))
print("MISSING (in md, no citation):", sorted(missing))
print("UNUSED (citation, not in md):", sorted(extra))

payload = {"markdown": md, "citations": citations}
(root / "render_input.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
print("WROTE render_input.json, size:", len(json.dumps(payload, ensure_ascii=False)))
