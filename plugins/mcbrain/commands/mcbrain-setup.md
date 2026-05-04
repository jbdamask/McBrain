---
name: mcbrain-setup
description: Provision a new McBrain knowledge-base vault. Routes directly to the mcbrain-setup SKILL — bypasses Cowork's plugin-builder.
argument-hint: "[name] (e.g., mcbrain-house, finance, ai-science)"
---

# /mcbrain-setup

Provision a new McBrain vault by running the `mcbrain-setup` SKILL
end-to-end.

## This is NOT a plugin-builder invocation

The McBrain plugin is already built and shipped. This command provisions
a *new vault* using the existing plugin. **Do not** render an intake
card with project-type selectors ("Home maintenance", "Renovation &
projects", etc.). **Do not** ask "What will this McBrain be for?". **Do
not** ask "Which skills/commands would you like included?". Those are
plugin-builder behaviors and they don't apply here.

Use the SKILL's prescribed `AskUserQuestion` shapes for multi-choice
intake (OS, backup strategy, gh installed, Notion DB intent) and plain
conversational asks for free-text inputs (vault name, path, GitHub
username, version paste-backs). The full intake list is in the SKILL's
"Required intake" section — follow it verbatim, don't add questions.

## Pre-filled argument

The user provided: `$ARGUMENTS`

If `$ARGUMENTS` is non-empty, treat it as the user's chosen McBrain
name and derive `MCP_NAME` from it:

- If it already starts with `mcbrain-`, use it as-is (e.g. `mcbrain-house` → `mcbrain-house`).
- Otherwise prefix it (`finance` → `mcbrain-finance`, `AI Science` → `mcbrain-ai-science`).

This skips Step 1's name question. Confirm the derived `MCP_NAME` with
the user in one short sentence, then continue to Step 2 (backup strategy)
without re-asking the name. **If the user wants to change it, accept
the change and move on.**

If `$ARGUMENTS` is empty, run Step 1 normally (ask for the name).

## Run the SKILL

Invoke the `mcbrain-setup` SKILL and follow it from Step 0 (or Step 1
if `$ARGUMENTS` filled the name) through Step 10. Don't paraphrase the
SKILL's prescribed `AskUserQuestion` payloads — use them verbatim.
