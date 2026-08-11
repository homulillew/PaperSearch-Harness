import json, pathlib
root = pathlib.Path(r"c:\Users\wushuhong\Desktop\literature-research-clean-e2e-20260810-195219\workspace")
out = json.loads((root / "render_out.json").read_text(encoding="utf-8-sig"))
content = out["result"]["content"]
(root / "rendered_report.md").write_text(content, encoding="utf-8")
print("Wrote rendered_report.md, length:", len(content))
