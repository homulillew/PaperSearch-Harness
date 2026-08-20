import json, subprocess, os
RUN="run_2ca834ac-48de-4ce8-a325-bc70a7aa760f"
REPO="c:/Users/wushuhong/Desktop/PaperSearch-Harness"
SCRATCH=REPO+"/.scratch_e2e/"+RUN
SKILL=REPO+"/.claude/skills/literature-research"
harness=SKILL+"/scripts/harness.py"
ws=REPO+"/workspace"
PYEXE=SKILL+"/.venv/Scripts/python.exe"
exprs = [
    r"2^{4} C_{U}^{\mathrm{INT4}}",
    r"2^{4} \cdot C_U^{\rm INT4}",
    r"2^{4} \cdot C_U^{\text{INT4}}",
    r"2^{4} \cdot C_U^{INT4}",
    r"a+b",
    r"\frac{a}{b}",
    # the actual offending expression from the manuscript
    r"2^{4}\cdot C_{U}^{\text{INT4}} + C_{L}^{\text{INT4}}",
]
for e in exprs:
    md = "# T\n\nSee $"+e+"$ here.\n"
    payload={"markdown":md,"citations":[]}
    p=SCRATCH+"/test_rw.json"
    with open(p,"w",encoding="utf-8") as f:
        json.dump(payload,f,ensure_ascii=False)
    env=dict(os.environ); env["PYTHONUTF8"]="1"
    r=subprocess.run([PYEXE,harness,"--workspace",ws,"put-report-manuscript","--run-id",RUN,"--input",p],capture_output=True,text=True,env=env)
    try:
        d=json.loads(r.stdout)
    except Exception:
        d={"ok":False,"raw":(r.stdout+r.stderr)[:200]}
    status="ACCEPTED" if d.get("ok") else "REJECT: "+(d.get("error",{}).get("message") if isinstance(d.get("error"),dict) else str(d)[:120])
    print(repr(e[:50]),"->",status)
