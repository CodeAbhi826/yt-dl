# DeepSeek Prompt — Fix broken dashboard.js (commit 99f325e regression)

> **Bug:** Commit `99f325e` (master toggle feature) accidentally deleted a critical line in `dashboard.js`, causing a syntax error that breaks the ENTIRE dashboard.
> **Symptom:** Dashboard loads blank — no sections, no footer text, no SSE connection. Browser console shows `Unexpected token ')'`.
> **Root cause:** The `clearBtn.addEventListener("click", function() {` line was deleted during the merge, leaving the handler body as top-level code with a stray `});` at the end.
> **Fix:** 1-line restoration. Verified working after fix.

---

## The bug

**File:** `src/static/dashboard.js` lines 390-398

### Current (BROKEN) code

```js
  const clearBtn = document.getElementById("clear-completed");
  if (clearBtn) {
      const completed = prevJobs.filter(j => j.status === 'completed');
      if (!completed.length) return;
      if (!confirm("Delete " + completed.length + " completed download(s) AND their files from disk?")) return;
      fetch("/api/bulk/delete", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({ids: completed.map(j => j.id)})})
        .then(r => r.json()).then(d => { showToast("Deleted " + d.deleted + " files"); });
    });
  }
```

### Problems

1. The `clearBtn.addEventListener("click", function() {` line is MISSING — the click handler body is floating as top-level code inside `if (clearBtn)`.
2. There's a stray `});` at the end (line 397) that was the original close of the addEventListener callback.
3. Node.js syntax check fails: `SyntaxError: Unexpected token ')' at line 397`.
4. Browser refuses to load the rest of `dashboard.js` → no `renderDashboard()`, no `connectSSE()`, no footer updates, no section rendering.
5. The `return` statements at lines 393-394 would also be invalid at top-level (not inside a function), but the syntax error hits first.

### How this happened

The toggle feature merge (commit `99f325e`) added new code above the `clearBtn` block. During the merge, the line `clearBtn.addEventListener("click", function() {` was accidentally deleted, but the body and closing `});` were left in place. This is a classic merge/edit error.

Verified via `git diff 44302b7..99f325e -- src/static/dashboard.js`:
```
   const clearBtn = document.getElementById("clear-completed");
   if (clearBtn) {
-    clearBtn.addEventListener("click", function() {
       const completed = prevJobs.filter(j => j.status === 'completed');
       if (!completed.length) return;
```

The `-` line shows what was deleted.

---

## The fix

**File:** `src/static/dashboard.js` lines 390-398

Replace the broken block with:

```js
  const clearBtn = document.getElementById("clear-completed");
  if (clearBtn) {
    clearBtn.addEventListener("click", function() {
      const completed = prevJobs.filter(j => j.status === 'completed');
      if (!completed.length) return;
      if (!confirm("Delete " + completed.length + " completed download(s) AND their files from disk?")) return;
      fetch("/api/bulk/delete", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({ids: completed.map(j => j.id)})})
        .then(r => r.json()).then(d => { showToast("Deleted " + d.deleted + " files"); });
    });
  }
```

**Key change:** Restore the `clearBtn.addEventListener("click", function() {` line (line 392 in the fixed version). Also fix indentation of the body to match the surrounding style (2-space indent inside the addEventListener callback).

---

## Verification

### Syntax check
```bash
node -c src/static/dashboard.js
# Should output: nothing (exit 0)
# Was: SyntaxError: Unexpected token ')' at line 397
```

### Browser test
1. Start the daemon: `python3 src/app.py`
2. Open `http://localhost:5000/` in browser
3. Open DevTools → Console — should be NO syntax errors
4. Dashboard should render:
   - DOWNLOADING section with active jobs in 2-col grid
   - QUEUED section with queued jobs in 3-col grid
   - FAILED section with failed jobs in 3-col grid
   - Footer shows "⬇ N active • N queued"
   - Master toggle shows "Downloads: ON" (green)
   - "+ Bulk add" button visible

### Functional tests
- [ ] `node -c src/static/dashboard.js` exits 0 (no syntax error)
- [ ] Dashboard renders all sections (DOWNLOADING, QUEUED, FAILED, RECENT)
- [ ] Footer text shows active/queued counts
- [ ] Master toggle button works (click → "Downloads: OFF", click again → "Downloads: ON")
- [ ] "+ Bulk add" button opens the modal
- [ ] "Delete completed" button shows confirmation dialog when clicked
- [ ] "Pause all" button still works
- [ ] SSE stream connects (queue updates live)
- [ ] No `Unexpected token` errors in browser console

---

## Reference: confirmed test results

```
[Before fix — commit 99f325e]
node -c src/static/dashboard.js
  → SyntaxError: Unexpected token ')' at line 397
Browser console:
  → PAGE ERROR: Unexpected token ')'
Dashboard state:
  → sections: [] (empty)
  → footer: '' (empty)
  → toggle: class='toggle' (no 'on' class — JS never ran to load state)
  → No SSE connection

[After fix — restoring the addEventListener line]
node -c src/static/dashboard.js
  → (no output, exit 0)
Browser console:
  → 0 page errors (only expected 404s for fake YouTube thumbnails)
Dashboard state:
  → sections: ['DOWNLOADING (2)', 'QUEUED (3)', 'FAILED (4)']
  → footer: '⬇ 2 active • 3 queued'
  → toggle: class='toggle on' label='Downloads: ON'
  → grids: grid-2 (2 cols, 2 children), grid-3 (3 cols, 3 children), grid-3 (3 cols, 4 children)
  → scrollWidth=1366px = clientWidth (no overflow)
  → Toggle click works: ON → OFF
  → Bulk-add modal opens
```

## Deliverable

Apply the 1-line fix (restore `clearBtn.addEventListener("click", function() {`).
Commit message: `fix(#75): restore clearBtn.addEventListener line deleted in toggle merge (99f325e regression)`.
