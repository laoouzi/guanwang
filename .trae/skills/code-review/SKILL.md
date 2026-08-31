---
name: "code-review"
description: "Reviews code for bugs, performance issues, security risks, and best practices, then applies optimizations. Invoke when user asks for code review, code optimization, or quality checks before committing or merging."
---

# Code Review & Optimization

This skill reviews code changes and applies targeted optimizations to improve quality, performance, and maintainability.

## Workflow

1. **Determine review scope**
   - If reviewing changes: use `git diff` / `git status` to identify modified files.
   - If reviewing specific code: read the target files fully before commenting.

2. **Review along these axes**
   - **Correctness**: logic errors, off-by-one mistakes, unhandled edge cases, race conditions, incorrect error handling.
   - **Performance**: unnecessary allocations, redundant loops or repeated computations, N+1 queries, missing early exits, inefficient data structures.
   - **Security**: injection risks, unsafe deserialization, hardcoded secrets, improper input validation at system boundaries.
   - **Readability**: unclear naming, overly long functions, deep nesting, magic numbers.
   - **Consistency**: adherence to the project's existing conventions and patterns.

3. **Classify findings by severity**
   - **Critical**: bugs, security vulnerabilities, data-loss risks — must fix.
   - **Major**: performance bottlenecks, significant maintainability issues — should fix.
   - **Minor**: style, naming, small readability improvements — optional.
   - **Suggestion**: alternative approaches worth considering.

4. **Apply optimizations (when requested)**
   - Fix issues in descending severity order.
   - One logical change at a time; keep changes minimal and focused.
   - Re-run tests/build after changes to verify nothing broke.

## Review Rules

- Read before judging: never comment on code you have not read in full context.
- Cite exact file and line locations for every finding.
- Provide a concrete fix or patch suggestion for each issue, not just a description of the problem.
- Respect existing project style; do not impose personal preferences as "issues".
- Distinguish facts (bugs, security holes) from opinions (style preferences).
- Do not refactor unrelated code that is not part of the review scope.

## Output Format

Report findings grouped by severity, each with:

- Location (file + line range)
- Issue description
- Impact
- Recommended fix (with code snippet when helpful)

End with a short overall assessment and whether the changes are ready to commit or merge.
