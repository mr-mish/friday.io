# CLAUDE.md

Guidance for AI assistants (and humans) working in this repository.

## Project

**friday.io** — a project management platform.

## Current state

> **This is a greenfield repository.** As of this writing it contains only
> `README.md` and this file — there is no application code, build tooling,
> dependency manifest, or test suite yet.
>
> Do not assume a language, framework, or architecture. Nothing about the
> stack has been decided in-repo. When you add the first real code, update
> this file in the same change so the sections below stop being placeholders
> and start describing reality.

## Repository layout

```
.
├── README.md    Project one-liner
└── CLAUDE.md    This file
```

There are no other tracked files. Run `git ls-files` to confirm the current
contents before assuming anything exists.

## Working conventions

Until the project establishes its own conventions, follow these:

- **Match what you find.** Once code exists, mirror its structure, naming,
  formatting, and idioms rather than importing patterns from elsewhere.
- **Keep this file current.** Any change that introduces a stack, a build
  step, a test command, or a directory convention must update the relevant
  section below in the same commit. A CLAUDE.md that lies is worse than none.
- **Small, described commits.** Write clear, imperative commit messages that
  explain the change.
- **Don't invent scope.** Build what is asked. Flag missing decisions (stack,
  DB, auth, hosting) rather than silently picking one and building on it.

## Development workflow

_No build, run, lint, or test commands exist yet._ When the toolchain is
chosen, document the canonical commands here, for example:

```
# install dependencies
# start the app / dev server
# run the test suite
# run the linter / formatter
# build for production
```

Replace the above with the real commands as soon as they exist. Prefer the
exact invocation a contributor should copy-paste.

## Git & branching

- Default branch: `main`.
- Do all work on a feature branch; never commit directly to `main`.
- Push with `git push -u origin <branch-name>`.
- Open a pull request only when explicitly requested.

## Architecture notes

_To be written once the platform takes shape._ When it does, capture here the
things that aren't obvious from reading a single file: how the major pieces fit
together, where the boundaries are, and any decisions a newcomer would
otherwise have to reverse-engineer.
