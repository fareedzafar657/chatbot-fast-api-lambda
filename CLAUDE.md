## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)



# Agent Rules

### 0. Git

Don't automatically add files in staging.

## 1. Think Before Coding
Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

---

## 2. Simplicity First
Minimum code that solves the problem. Nothing speculative.

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.
- Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

---

## 3. Surgical Changes
Touch only what you must. Clean up only your own mess.

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.
- The test: Every changed line should trace directly to the user's request.

---

## 4. Goal-Driven Execution
Define success criteria. Loop until verified.

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]

---

## 5. Never Break Working Code
If it works, don't touch it without reason.

- Run existing tests before and after every change.
- If no tests exist, state that explicitly before proceeding.
- If your change breaks something unrelated, stop and report it.
- Don't silently fix unrelated bugs — mention them, let the user decide.
- Prefer reversible changes over irreversible ones.

---

## 6. Communicate Clearly
Say what you did, what you didn't do, and why.

After implementing:
- Summarize what changed in plain language.
- List any assumptions you made.
- Flag anything you're unsure about.
- If you hit a dead end, say so — don't loop silently.
- Don't pad responses. One clear sentence beats three vague ones.

When you can't do something:
- Say so immediately. Don't attempt a workaround without asking.
- Name the constraint. Don't make the user guess why you stopped.

---

## 7. File and Scope Awareness
Know what you're touching and why.

Before editing a file:
- Read the entire file first, not just the relevant section.
- Understand the file's purpose before changing anything.
- If the file is large, summarize your understanding before proceeding.

When creating new files:
- Confirm the file doesn't already exist.
- Place it in the correct location per the project structure.
- Follow existing naming conventions.

---

## 8. Security and Secrets
Never introduce security regressions.

- Never hardcode secrets, API keys, tokens, or passwords.
- Never log sensitive data (tokens, passwords, user PII).
- Never disable authentication or authorization, even temporarily.
- If a change touches auth, input validation, or data access — flag it explicitly.
- Treat all user input as untrusted.

---

## 9. Environment Awareness
Know which environment you're targeting.

- Never run destructive commands (drop table, delete, rm -rf) without explicit confirmation.
- Never modify production configuration unless explicitly asked.
- If a command is irreversible, say so before running it.
- Distinguish between dev, staging, and production clearly.

---

## 10. Dependencies
Don't add dependencies without justification.

- Prefer standard library over third-party packages.
- If adding a package: state why, what it does, and if there's a lighter alternative.
- Never add a dependency for a one-liner you could write yourself.
- Check if the project already has a package that does the same thing.