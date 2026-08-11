import json, pathlib
root = pathlib.Path(r"c:\Users\wushuhong\Desktop\literature-research-clean-e2e-20260810-195219\workspace")
out = json.loads((root / "render_out.json").read_text(encoding="utf-8-sig"))
content = out["result"]["content"]
publish = {"content": content}
(root / "publish_input.json").write_text(json.dumps(publish, ensure_ascii=False), encoding="utf-8")
print("Wrote publish_input.json, content length:", len(content))
