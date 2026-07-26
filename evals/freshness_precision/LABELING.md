# Labeling instructions — freshness precision corpus

You are labeling ground truth for a measurement. You must not look at any
implementation, documentation, or test of the system being measured; your
labels are only valid if they are independent of it. Everything you need is
in `cases.json` and this file.

## What each case is

Each case describes one repository at two points in time:

- `files_before` — the repository content when a fact was verified.
- `files_after` — the changes one landed commit made (a path mapped to
  `null` was deleted; paths not mentioned are unchanged).
- `command` — the verification command (`{python}` means the Python
  interpreter). The verified record's claim is exactly: **running this
  command from the repository root exits with code 0.**

## The question you are answering

> After this commit, should the verified record have been invalidated?

Label **yes** when running the command honestly against the post-commit
tree would no longer exit 0, or when the claim's subject no longer exists
in a form the claim describes (for example, the module the command checks
was deleted and nothing equivalent answers for it).

Label **no** when the command would still exit 0 and the claim still means
what it meant when it was verified.

Label **uncertain** when you genuinely cannot tell. Uncertain is an honest
answer and is reported as such; do not force a yes or no.

## How to decide

Reason from the file contents, or — encouraged, when practical —
reconstruct the post-commit tree in a scratch directory from the JSON and
actually run the command. Deciding by execution is legitimate: the
definition above is behavioral, not structural. What you must NOT do is
consult the measured system or guess what it would say.

## Output format

Write `labels.json` next to `cases.json`:

```json
[
  {"case_id": "...", "label": "yes" | "no" | "uncertain",
   "rationale": "one or two sentences"}
]
```

One entry per case, every case labeled, rationale mandatory.
