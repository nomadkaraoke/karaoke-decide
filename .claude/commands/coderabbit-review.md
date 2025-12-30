---
description: Review and address CodeRabbit comments on current PR
allowed-tools: Read, Glob, Grep, Edit, Write, Bash, mcp__github__pull_request_read, mcp__github__get_me, mcp__github__list_pull_requests, mcp__github__add_issue_comment
---

# CodeRabbit Comment Review

You are reviewing the current PR to address all CodeRabbit automated review comments.

## Your Task

### 1. Identify the Current PR

First, determine the current branch and find the associated PR:
```bash
git branch --show-current
```

Then use GitHub tools to find the PR for this branch in the `nomadkaraoke/karaoke-decide` repository.

### 2. Fetch CodeRabbit Comments

Get all reviews, review comments, and regular comments on the PR:

**PR Reviews (MOST IMPORTANT - Contains Nitpicks and Issues)**
- Use `pull_request_read` with `method: "get_reviews"` to get all PR reviews
- Look for reviews from the `coderabbitai[bot]` user
- The review `body` field contains the detailed analysis with:
  - "Nitpick comments" section with specific code suggestions
  - "Potential issues" flagged in the diff
  - Code improvement suggestions with file paths and line numbers
- **This is the primary source of CodeRabbit feedback** - parse this carefully

**Review Comments (Inline Code Comments)**
- Use `pull_request_read` with `method: "get_review_comments"` to get inline code comments
- These are comments attached to specific lines in the diff
- Also check for `coderabbitai[bot]` comments here

**PR Comments (Summary)**
- Use `pull_request_read` with `method: "get_comments"` to get regular PR comments
- CodeRabbit posts a walkthrough/summary comment here
- Less actionable than the review body, but may contain additional context

### 3. Analyze Each Comment

For each CodeRabbit comment:
1. Read the comment carefully to understand the concern
2. Look at the referenced code location
3. Decide if a code change is warranted:
   - **Make changes** for: valid bugs, security issues, clear improvements, style violations
   - **Skip changes** for: subjective preferences, false positives, already addressed, not applicable
4. If skipping, note the reason

### 4. Make Code Changes

For each comment requiring action:
1. Navigate to the file and line mentioned
2. Understand the surrounding context
3. Make the minimal change needed to address the concern
4. Verify the change doesn't break anything obvious

### 5. Commit and Push

After making all changes:
```bash
# Stage all changes
git add -A

# Commit with descriptive message
git commit -m "fix: address CodeRabbit review comments

- [list each fix briefly]

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"

# Push to the PR branch
git push
```

### 6. Resolve Threads

After pushing:
- Reply to each CodeRabbit review thread indicating what was done
- If a comment was addressed, reply with what was changed
- If a comment was intentionally skipped, explain why
- This helps mark threads as resolved so the PR can be merged

## Output Format

Provide a summary report:

```
## CodeRabbit Review Summary

### Comments Addressed
- [ ] File:line - Brief description of issue → What was changed

### Comments Skipped (with reason)
- [ ] File:line - Issue → Reason for skipping

### Actions Taken
- Committed: [commit hash]
- Pushed to: [branch name]
- Threads replied to: [count]

### Status
Ready for merge: Yes/No (if No, explain why)
```

## Guidelines

- Focus on code quality improvements, not just making CodeRabbit happy
- Don't blindly accept every suggestion - use judgment
- Group related fixes into logical commits if needed
- Keep changes minimal and focused on the specific issues raised
- If a suggestion would require significant refactoring, note it for future work instead
