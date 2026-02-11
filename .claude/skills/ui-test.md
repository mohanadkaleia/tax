---
name: ui-test
description: Run Playwright tests, capture screenshots, and visually verify that UI changes match the user's request. Iterate until the visual output matches the goal.
user_invocable: true
---

# UI Visual Verification Loop

Run Playwright e2e tests to capture screenshots and visually verify that your UI changes match the user's original request.

## Purpose

This is NOT just about passing tests. The goal is to:
1. Capture the current state of the UI via screenshots
2. Visually verify that changes match what the user asked for
3. Iterate until the visual output matches the user's intent

## Instructions

### 1. Understand the Goal

Before running tests, be clear about what you're verifying:
- What did the user ask for? (e.g., "change button color to red", "make sidebar wider")
- What visual change should you see in the screenshots?
- Which page/component should show this change?

### 2. Clean and Run Tests
```bash
npm run ui:clean && npm run ui:check
```

### 3. Analyze Screenshots Against the Goal

Read the relevant screenshots from `artifacts/playwright/screenshots/`:
```
Read: artifacts/playwright/screenshots/{test-name}-{step}-{description}.png
```

**Ask yourself:**
- Does the screenshot show the change the user requested?
- Is the button actually red? Is the sidebar actually wider?
- Does it match the user's intent, not just "does the test pass"?

### 4. Verification Decision

**If the visual matches the user's request:**
- Report success with evidence: "The button is now red as you requested. See screenshot X."
- Show the relevant screenshot to confirm

**If the visual does NOT match:**
- Identify what's wrong: "The button is still blue, not red"
- Determine why: wrong CSS class, wrong selector, style not applied
- Make the fix
- Run the loop again

### 5. Iterate Until Visual Goal is Met

```
┌─────────────────────────────────────────────────────────────┐
│  User Request: "Change the login button to red"            │
│                           ↓                                 │
│  1. Edit Vue component (add red styling)                   │
│                           ↓                                 │
│  2. Run: npm run ui:clean && npm run ui:check              │
│                           ↓                                 │
│  3. Read screenshot of login page                          │
│                           ↓                                 │
│  4. Is the button red? ─────┬─── YES → Done! Show proof    │
│                             │                               │
│                             NO                              │
│                             ↓                               │
│  5. Analyze why (wrong class? not applied?)                │
│                             ↓                               │
│  6. Fix the issue                                          │
│                             ↓                               │
│                      Loop back to step 2                    │
└─────────────────────────────────────────────────────────────┘
```

## Example Scenarios

### Scenario 1: Color Change
**User:** "Make the publish button green"

**Agent workflow:**
1. Edit `WritePage.vue`, add green class to publish button
2. Run `npm run ui:clean && npm run ui:check`
3. Read `editor-publish-03-publish-clicked-*.png`
4. Look at the button - is it green?
5. If not green → fix CSS, repeat
6. If green → "Done! The publish button is now green. See screenshot."

### Scenario 2: Layout Change
**User:** "Make the sidebar 300px wide"

**Agent workflow:**
1. Edit sidebar component, set width to 300px
2. Run tests
3. Read dashboard screenshots
4. Visually check: does sidebar look wider?
5. If not → inspect CSS, maybe conflicting styles, fix
6. If yes → "The sidebar is now 300px wide. Verified in screenshot."

### Scenario 3: New Element
**User:** "Add a logout button to the settings page"

**Agent workflow:**
1. Add logout button to `SettingsPage.vue`
2. Run tests
3. Read `settings-display-*.png`
4. Is there a logout button visible?
5. If not visible → check positioning, z-index, conditionals
6. If visible → "Logout button added. See screenshot."

## Key Principle

**Tests passing ≠ Goal achieved**

A test might pass because:
- The element exists (but wrong color)
- The page loads (but layout is broken)
- No errors thrown (but feature doesn't work visually)

Always verify the **visual output** matches the **user's intent**.

## Commands Reference

| Command | Purpose |
|---------|---------|
| `npm run ui:check` | Run all tests, capture screenshots |
| `npm run ui:debug` | Run with visible browser (for debugging) |
| `npm run ui:clean` | Clear old screenshots before fresh run |
| `npm run ui:report` | Open HTML report |

## Screenshot Locations

- Step screenshots: `artifacts/playwright/screenshots/`
- Naming pattern: `{test-name}-{step-number}-{description}-{timestamp}.png`

## When to Stop Iterating

Stop when you can confidently say:
> "I have verified in the screenshot that [specific user request] is now visible/working. Here is the proof: [screenshot path]"
