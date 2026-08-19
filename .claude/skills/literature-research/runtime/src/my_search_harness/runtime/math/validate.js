// Mechanical TeX renderability preflight for report manuscripts.
//
// Reads a JSON array of TeX expression strings from stdin (one batch), loads
// MathJax exactly once, and writes a JSON array of {"ok": bool, "error": string|null}
// results to stdout — one per input expression, in order.
//
// This is deliberately NOT a TeX parser and NOT a semantic validator. It asks
// the real target renderer (MathJax) one question per expression: "can you
// render this?" MathJax signals rejection either by emitting an merror node
// (carrying a data-mjx-error="..." attribute) or by throwing; both are caught.
//
// Exit codes:
//   0 — validated every expression (results are in stdout, per-expression)
//   2 — catastrophic failure (bad input JSON, or MathJax could not load). The
//       human-readable reason is on stderr; no stdout is produced.
//
// Usage:
//   node validate.js < input.json > results.json
//
// The caller (math_preflight.py) never inspects the exit code for per-expression
// failures — those live in the result payload. The exit code only distinguishes
// "ran the batch" from "could not run at all".

"use strict";

const { mathjax } = require("mathjax-full/js/mathjax.js");
const { TeX } = require("mathjax-full/js/input/tex.js");
const { SVG } = require("mathjax-full/js/output/svg.js");
const { liteAdaptor } = require("mathjax-full/js/adaptors/liteAdaptor.js");
const { RegisterHTMLHandler } = require("mathjax-full/js/handlers/html.js");
const { AllPackages } = require("mathjax-full/js/input/tex/AllPackages.js");

// Load MathJax once. The liteAdaptor needs no DOM; AllPackages loads the AMS
// environments so valid matrices/cases/align are not false-rejected.
const adaptor = liteAdaptor();
RegisterHTMLHandler(adaptor);
const tex = new TeX({ packages: AllPackages });
const svg = new SVG();
const doc = mathjax.document("", { InputJax: tex, OutputJax: svg });

const ERROR_MARKER = /data-mjx-error="([^"]*)"/;

function validate(expr) {
  try {
    const node = doc.convert(expr);
    const out = adaptor.innerHTML(node);
    const match = out.match(ERROR_MARKER);
    if (match) {
      return { ok: false, error: match[1] };
    }
    return { ok: true, error: null };
  } catch (err) {
    // Some malformed input makes MathJax throw rather than emit merror.
    return { ok: false, error: String(err && err.message ? err.message : err) };
  }
}

function main() {
  let input = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (chunk) => {
    input += chunk;
  });
  process.stdin.on("end", () => {
    let expressions;
    try {
      const parsed = JSON.parse(input);
      if (!Array.isArray(parsed)) {
        throw new Error("input must be a JSON array of TeX strings");
      }
      expressions = parsed.map((value) => {
        if (typeof value !== "string") {
          throw new Error("every expression must be a string");
        }
        return value;
      });
    } catch (err) {
      process.stderr.write(`math validator: bad input: ${err.message}\n`);
      process.exit(2);
    }
    const results = expressions.map(validate);
    process.stdout.write(JSON.stringify(results));
    process.stdout.write("\n");
  });
}

main();
