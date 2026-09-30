# Implementation ledger — PLAN.md

## Workspace and source notes

- `PLAN.md` is the user's approved implementation target. Its sample scenarios and architecture are project requirements; no embedded text asks for actions outside this workspace.
- No script, business-rule package, or customer corpus was supplied. The first runnable version will use conspicuously synthetic demo records and scripts; it will refuse unsupported claims and document the replacement path.
- The workspace was empty apart from `PLAN.md` and is not a Git repository. Work in the requested directory because there is no repository state or branch to isolate. Cost if wrong: edits have no Git snapshot until the user initializes version control.
- Cloud credentials are not configured for this local build. Use browser speech APIs behind a small adapter, and report their browser/provider limitations instead of claiming Azure or LiveKit configuration.
- The plan's recommended LiveKit/Azure/pgvector deployment cannot be configured without service accounts or an approved production environment. This implementation uses the Python standard library, plain HTML/CSS/JavaScript, JSON records, and local browser APIs.
- PDF parsing is available through the bundled `pypdf` runtime; application logic will otherwise use the Python standard library.
- Pre-flight: no shared implementation interfaces exist yet. The plan is a delivery roadmap rather than a file-level plan; this implementation will define and test module contracts as it proceeds.

## Rulings

- Ruling: use synthetic, explicitly labeled demo policies and scripts — supplied business content is absent — cost if wrong: domain wording and policy accuracy must be re-reviewed when real source material is added.
- Ruling: use local browser ASR/TTS with typed and text-replay fallbacks — cloud credentials are absent and the demo must run locally — cost if wrong: provider-specific quality, voice configuration, and accent claims remain unverified.
- Ruling: store voice recordings only after an explicit in-app recording consent, under ignored local evidence storage — the brief requires call evidence while the plan forbids customer PII — cost if wrong: human-recorded evidence still needs a speaker's informed consent and must use test personas.
- Ruling: use a standard-library local HTTP service, vanilla browser UI, JSON records, and weighted lexical retrieval rather than the recommended React/LiveKit/Azure/PostgreSQL-vector stack — there are no credentials, infrastructure, or repository dependencies and the deliverable must run locally — cost if wrong: no real-time audio transport, vector search, cloud ASR/TTS, or production throughput is demonstrated.
- Ruling: implement callback and CRM actions as an in-memory/mock intent only — no authorized CRM or telephony destination was supplied — cost if wrong: real integrations and their consent, access control, and failure handling remain future work.
- Ruling: treat browser-level recorded-call evidence and language-quality measurements as unverified — this environment did not grant microphone access or supply consented speakers — cost if wrong: the original five-call and localized-recording acceptance gates remain unmet until a human runs the consented test flows.

## Phases

- [x] Phase 1: traceable knowledge ingestion, schema, retrieval, and synthetic evaluation implemented; uses lexical retrieval, not embeddings.
- [x] Phase 2: grounded renewal conversation, qualification, escalation, browser speech adapter, and mock action implemented; real audio calls were not recorded here.
- [x] Phase 3: Filipino/Taglish and Indonesian text flows and adaptation examples implemented; native-speaker, ASR, and accent tests remain outstanding.
- [x] Phase 4: transcript-text signal detection, nudge controls, dashboard, and latency instrumentation implemented; streaming audio replay/latency measurement remains outstanding.
- [x] Phase 5: setup docs, reproducible synthetic reports, tests, and code review completed; real call recordings and walkthrough video remain outstanding.

## Remaining acceptance evidence

- Five Q1 audio calls, at least two or three calls per locale as required by the selected gate, four real-time audio replays, consented regional-accent testing, native-speaker/compliance review, and a walkthrough video require human speakers and an approved recording environment.
- Synthetic test reports are saved under `evidence/`; they are logic/retrieval evaluations, not audio evidence. No user microphone permission was accepted during implementation.

## Verification record

- Baseline: no app/tests existed. Python runtime: 3.12.14. `pytest` absent; use `unittest`. Bundled `pypdf` is present.
- Final verification: 63 `unittest` tests pass; Python compileall and Node syntax checks pass; 3 browser-runtime tests pass; 11/11 scripted call logic scenarios and 11/11 retrieval/unsupported queries pass.
- Local API smoke check: health reports local synthetic mode and no configured LLM; a renewal question returns a safe spoken answer with approved citation `ph_renewal_window`.
- Scripted result labels `audio_recordings: 0`; UI smoke test was limited to rendering and loaded localized content because browser interaction automation did not activate controls. No microphone permission was accepted.
- README and `.env.example` document setup, source review/import, environment variables, verification, architecture, evidence controls, and known limitations. `.gitignore` excludes local imports, environment values, and call recordings.
- Final review: fresh-context review completed. All 13 important findings were fixed and regression-tested in one pass; both minor findings were addressed. Remaining evidence gaps are listed above and are deliberate human-audio / production-integration gates, not unreviewed code defects.
