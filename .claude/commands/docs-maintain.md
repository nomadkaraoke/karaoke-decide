---
description: Periodic documentation maintenance and organization check
allowed-tools: Read, Glob, Grep, Edit, Write, Bash
---

# Documentation Maintenance Check

You are performing periodic maintenance on the project documentation.

## Checks to Perform

### 1. Structure Compliance
Verify docs/ follows the correct structure:
```
docs/
├── README.md              # Current status + navigation
├── ARCHITECTURE.md        # System design
├── DEVELOPMENT.md         # Dev setup, testing, deployment
├── API.md                 # Backend API reference
├── LESSONS-LEARNED.md     # Accumulated wisdom
└── archive/               # Historical docs (YYYY-MM-DD-topic.md)
```

Report any files that don't belong or are misplaced.

### 2. Freshness Check
- Read `docs/README.md` - is the status section current?
- Check recent git commits - do they suggest docs should be updated?
- Look for stale information that contradicts current code

### 3. Archive Organization
- Verify all archive files have YYYY-MM-DD prefix
- Check for any docs that should be archived (completed work, old plans)
- Remove truly obsolete content (empty files, duplicate info)

### 4. Cross-Reference Check
- Verify CLAUDE.md points to current doc locations
- Check that docs reference each other correctly
- Ensure no broken internal links

### 5. Content Quality
- Flag any docs over 500 lines (may need splitting)
- Identify duplicate information across docs
- Note sections that seem outdated

## Actions to Take

1. **Fix structural issues** - Move/rename misplaced files
2. **Update stale content** - Refresh outdated information
3. **Archive completed work** - Move old docs to archive/
4. **Report findings** - Summarize what was found and fixed

## Output Format

Provide a maintenance report:
```
## Documentation Maintenance Report - YYYY-MM-DD

### Issues Found
- [list of issues]

### Actions Taken
- [list of fixes made]

### Recommendations
- [suggestions for future attention]

### Status
- [ ] Structure: OK/Fixed
- [ ] Freshness: OK/Updated
- [ ] Archive: OK/Organized
- [ ] Cross-refs: OK/Fixed
- [ ] Quality: OK/Flagged
```
