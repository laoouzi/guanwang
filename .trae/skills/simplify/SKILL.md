---
name: "simplify"
description: "Simplifies codebases by removing complexity, dead code, and unnecessary abstractions while preserving behavior. Invoke when user asks to simplify code, reduce complexity, clean up the codebase, or refactor for clarity."
---

# Simplify Codebase

This skill systematically simplifies and declutters code while strictly preserving external behavior.

## Core Principles

1. **Behavior first**: Never change observable behavior. Simplification is refactoring, not rewriting features.
2. **Minimal complexity**: Prefer the least amount of code and abstraction that solves the problem at hand.
3. **Delete over abstract**: Three similar lines of code are better than a premature abstraction. Delete dead code completely instead of commenting it out.
4. **Trust boundaries**: Only validate at real system boundaries (user input, external APIs). Remove defensive checks for scenarios that cannot happen.
5. **No backward-compatibility hacks**: Remove unused variables, re-exported types, and "removed code" comments when the code is no longer needed.

## Workflow

1. **Map the target scope**
   - Confirm with the user which files, modules, or directories to simplify.
   - Read the code thoroughly before changing anything.

2. **Identify simplification opportunities**
   - Dead code: unused functions, variables, imports, unreachable branches.
   - Over-engineering: unnecessary abstraction layers, single-use helpers, speculative configurability, feature flags for hypothetical needs.
   - Redundant code: duplicate logic that can be consolidated without forcing an abstraction.
   - Overly clever code: patterns that can be replaced with plain, readable constructs.
   - Excessive comments/docstrings on code that is already self-evident.

3. **Simplify in safe increments**
   - Make one logical simplification at a time.
   - After each change, verify the code still compiles or passes existing tests.
   - Prefer editing existing files over creating new ones.

4. **Verify**
   - Run the project's build/test commands (if available) to confirm behavior is unchanged.
   - Summarize what was removed or simplified and why it is safe.

## Rules

- Do not add new features, new options, or new abstractions while simplifying.
- Do not add error handling for impossible scenarios.
- Do not rename or restructure code that is already simple.
- Keep public APIs unchanged unless the user explicitly allows breaking changes.
- When unsure whether code is unused, search the codebase for references first; if truly ambiguous, ask the user.

## Output

Report each simplification as: location (file + lines), what changed, and the reason. Group trivial bulk deletions (e.g., removing unused imports) into one summary item.
