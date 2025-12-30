---
description: Review current session for documentation updates before merging PR
allowed-tools: Read, Glob, Grep, Edit, Write
---

# Pre-Merge Documentation Review

You are reviewing this chat session to identify documentation updates needed before merging the current PR.

## Your Task

1. **Analyze the current session** - What work was done? What was learned? What changed?

2. **Check if docs need updates:**
   - Does `docs/README.md` status section need updating?
   - Should anything be added to `docs/LESSONS-LEARNED.md`?
   - Does `docs/ARCHITECTURE.md` need changes (if architecture changed)?
   - Does `docs/DEVELOPMENT.md` need changes (if dev workflow changed)?
   - Does `docs/API.md` need changes (if API changed)?

3. **For significant completed work:**
   - Create `docs/archive/YYYY-MM-DD-topic.md` summarizing the work
   - Use today's date and a descriptive topic name

4. **Update docs/README.md** with:
   - Current status if it changed
   - Any new known issues discovered
   - Link to new archive doc if created

## Guidelines

- **IMPORTANT**: Make all documentation changes on the current feature branch (git worktree), NOT on main. Check `git branch` or the worktree path to ensure you're editing files in the correct location.
- Keep updates concise and factual
- Focus on information useful for future AI agents
- Don't duplicate information that's in the code
- Archive docs should capture "what was done and why" not implementation details

## Output

After reviewing, either:
- Make the necessary documentation updates
- Report "No documentation updates needed" with brief explanation why

Remember: Good documentation helps the next agent continue where you left off.
