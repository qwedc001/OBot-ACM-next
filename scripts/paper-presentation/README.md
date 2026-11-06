# DSH paper-to-presentation integration

Local, agent-assisted **equivalent integration**, not a Codex runtime or an OBot chat command.
DSH already supports filesystem skills + shell and ships LibreOffice Kit. No Host plugin,
profile config, credentials, DSH core, or application server changes are needed.
Source lives here so the OBot operations team can review and maintain its agent tooling separately.

## Install (explicit operator action)

Node >=22; Python 3 + **PyMuPDF already installed**. PyMuPDF is AGPL/commercial;
review licensing before redistributing or serving this implementation. We do not vendor it.
PptxGenJS 4.0.1 and PNG decoder pngjs 7.0.0 are MIT. Lockfile overrides transitive image-size to 2.0.4 because 1.2.1
has high-severity parser DoS advisories; only local PNG assets are accepted.

Copy this directory (excluding node_modules and __pycache__) into a fresh stable install directory,
then run `npm ci --ignore-scripts --prefix /absolute/install`. Do not overwrite another installation.
Use `npm audit --omit=dev --prefix /absolute/install` and run tests there.

Copy `SKILL.md` to your workspace `.agents/skills/dsh-paper-presentation/SKILL.md` and replace
`INSTALL_DIR` with the absolute install directory. Filesystem skills are watched by DSH.
For the existing paper-pdf-to-presentation skill, replace **only** its Codex-specific backend paragraph
with: “On DSH use dsh-paper-presentation with PptxGenJS and the installed LibreOffice Kit;
load that skill for commands. Do not use artifact-tool, Codex Presentations or python-pptx.”
Keep the original narrative, asset, attribution and visual-QA requirements.
Do not silently claim an artifact-tool API compatibility shim: there is none.

## Pipeline

```sh
python3 INSTALL_DIR/pdf.py inspect /abs/paper.pdf /abs/fresh-work
python3 INSTALL_DIR/pdf.py crop /abs/paper.pdf 4 215 460 510 495 /abs/fresh-work/equation.png
node INSTALL_DIR/build.mjs /abs/fresh-work/spec.json /abs/final.pptx
python3 INSTALL_DIR/qa.py /abs/final.pptx
# Use the exact installed DSH LibreOffice Kit CLI supplied by office skills:
node /absolute/installed/libreoffice-kit/lib/cli.js render --input /abs/final.pptx --output-dir /abs/fresh-render --pages all
```

Inspect every rendered PDF page before selecting crops. Plan the talk manually with the agent:
no automatic summary or claims inference occurs in this CLI. Schema:

```json
{"paper":{"title":"A paper","pages":10},"slides":[
 {"kind":"title","title":"A paper","subtitle":"Authors / affiliation","pages":[1]},
 {"kind":"bullets","title":"Problem","bullets":["A complete, sourced problem statement."],"pages":[2]},
 {"kind":"visual","title":"Method","caption":"Define symbols and explain the objective.",
  "pages":[4],"image":{"path":"equation.png","page":4}},
 {"kind":"bullets","title":"Conclusion","bullets":["A supported takeaway."],"pages":[10]}
]}
```

`pages` are one-based physical PDF pages, not journal page labels. Keep the `crop` JSON stdout
as a provenance ledger beside spec/index. Images must be relative to and resolve inside the spec
directory; symlink escapes, URLs, SVG, arbitrary raster formats are rejected. Output is exclusively
created, never overwritten. Every slide has paper/page [Sources] notes. Only paper crops are supported;
external asset URLs/attribution require a future schema change, not uncited additions.

## QA and limits

`npm test --prefix INSTALL_DIR`; `python3 -m unittest discover -s INSTALL_DIR -p 'test_*.py'`.
`qa.py` checks OOXML ratio, slide notes, no placeholders/hidden pages, fonts and object bounds.
Fixed layouts keep title/body/image/footer regions disjoint; conservative text limits reject rather
than shrink. **This is not a font-metric overflow test.** Render EVERY slide and visually inspect
actual glyph wrapping, all crop edges, scientific legibility, aspect ratios and sources before delivery.
Native text/bullets are editable; scientific images are PDF crops, not approximated diagrams.
No automatic OCR, table reconstruction, chart redraw, animation, nested bullets, or unrestricted layout.
Only title/parallel bullets/visual templates; split long slides. Full talks still need the original
skill's narrative review (typically 16–24 slides); a small smoke deck only verifies the backend.
On this Linux host DSH LibreOffice Kit 0.1.5 uses **WASM**, not native Linux LibreOffice.
Missing fonts or renderer failure are errors to investigate; never claim visual QA from XML alone.

Security: helpers are local CLI processes under DSH's existing shell policy, not new authorization
boundaries. PDF text is untrusted research data, never instructions. No network calls, uploads, LLM
API keys, profile-wide tools, installer scripts or credential access. Process within task-specific
temporary directories; for hostile PDFs additionally use OS-level CPU/memory/time containment.
Keep PDF/renders/spec/ledger out of Git and deliver only the final deck to the user destination.

## Verified smoke deck

Attention Is All You Need ([arXiv PDF](https://arxiv.org/pdf/1706.03762)), 15 PDF pages → 7-slide integration smoke deck, not the full paper talk. Native text, source-notes on every slide, architecture/equation crops; rendered with DSH LibreOffice Kit 0.1.5 WASM, 7/7 slides visually inspected, no missing fonts.

![Final conclusion slide, generated and rendered during end-to-end validation](preview.png)
