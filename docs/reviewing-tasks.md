# Reviewing and correcting tasks

The Digital FTE number is only as good as each task's estimate. The app
estimates every task automatically, but **you know the work better than any
rule or AI model**. Ten minutes of review makes the number something you can
defend.

![The task editor](images/task-editor.png)

---

## Why review at all

The app estimates **H**, the hours a competent person would need without AI, in
two steps: rules count the evidence (lines written, files read, commands run),
then an AI service judges how hard the work really was. Both get some kinds of
work wrong in predictable ways. In a first review of 10 tasks:

| Kind of task | AI estimate compared with the owner's |
|---|---|
| Short notes, admin, small fixes | **too high**, about 2–3× |
| Half a day to a few days of work | **too low**, about 1.5–2× |
| A month-long build | **far too low**, about 5× |

Your corrections fix the tasks you touch, and the app **learns** from them to
adjust the ones you don't (see [How the app learns](#how-the-app-learns)).

---

## Opening a task

In the dashboard, go to the **DFTE** view and scroll to **Tasks**.

- **Click a task** to open its editor. Click it again, or **Close**, to fold it away.
- **Filter** with the chips above the list (*Project work*, *General & learning*,
  *Setup & admin*), pick a **month** at the top right, or click an **agent** to
  see only its tasks.
- **Link to one task:** `http://127.0.0.1:8765/?task=<task id>` opens the page on
  that task with its editor open. **Show all tasks** goes back to the full list.

A task you've corrected shows an **edited** tag; one marked as not real work
shows **not counted** and is struck through.

---

## What the editor shows

From top to bottom:

| Part | What it tells you |
|---|---|
| **AI reason** (e.g. *cortex: …*) | The AI's one-sentence reason for its estimate. *(said 28h, kept within 0.25–2×…)* means its answer was outside the guard rails and was moved to the nearest limit. |
| **Rules** | The rules estimate, its keep factor, and the evidence it counted: lines of code and documents, lines removed, files read, commands, words of answers. |
| **K** | Why the keep factor has its value, e.g. *32 edited files: 29 in git, 1 still on disk, 2 gone*. If the app has learned a factor for this category, it says so here. |

---

## What you can change

### How long without AI? (the size buttons)

The quickest way to correct a task. Sizes are easier to judge than hours. Click
the one that fits; it fills in the hours for you.

| Button | Means | Hours |
|---|---|---|
| **XS** | under an hour | 0.5 |
| **S** | a couple of hours | 2 |
| **M** | half a day to a day | 6 |
| **L** | 2–3 days | 20 |
| **XL** | about a week | 35 |
| **XXL** | 2 weeks | 70 |
| **Month** | a month | 140 |

Ask yourself: *how long would a competent person need to produce this same
result without AI*, including understanding the problem, looking things up,
writing, testing and polishing?

### Hours a person would need

The same number the size buttons fill in. Type an exact value if you know it.

### Keep factor

How much of the output you actually used, from 0 to 1.

| Value | Use it when |
|---|---|
| **1** | You used it as it was, or built on it |
| **0.5** | You used about half, or had to redo a good part |
| **0** | You threw it away (the task then adds nothing) |

### Purpose

Why the work was done. It changes where the task is grouped and, if you've set
weights, how much it counts.

| Purpose | For |
|---|---|
| **Project work** | Something for a project, customer or product |
| **General & learning** | Asking the AI to explain or teach, as you would ask a senior colleague |
| **Setup & admin** | Configuring or fixing your own tools and machine |

### Category

What kind of work it was. It decides which professional the hours are measured
against, and which group the app learns from.

| Category | Measured against |
|---|---|
| code | a mid-level developer |
| documentation, demo prep | a presales consultant |
| research, analysis | an analyst |

### Not real work

Tick it for things that shouldn't count at all: a test run, a question you
abandoned, chit-chat. The task stays in the list but adds nothing.

### Save, Reset to estimate, Close

- **Save** keeps your changes. The headline, charts and totals update at once.
- **Reset to estimate** removes your correction and goes back to the AI's (or the
  rules') estimate. It only appears on edited tasks.
- **Close** folds the editor without saving.

Only the fields you actually change are saved as your correction. The rest keep
following the app's estimate.

---

## How the app learns

Once you've corrected the hours on **3 or more tasks in the same category**, the
app works out your typical adjustment for that category and applies it to every
task in it that you haven't corrected.

- Example: after your code corrections, code tasks were on average 1.67× the AI's
  estimate, so the app now multiplies other code tasks by 1.67.
- The banner shows what it has learned, e.g. *learned: code ×1.67*, and the
  editor of an affected task says *learned factor ×1.67 for code*.
- The adjustment is kept between ×0.33 and ×3, so a few unusual corrections
  can't swing everything.
- Your own corrections always win over the learned factor.

---

## Big projects that span several tasks

A long piece of work, like building an app over a few weeks, is often split into
several tasks (a new task starts after a break of more than 30 minutes). It's
easier to judge the whole project than each part.

For now:

1. Filter to the project (it's named in each task's second line).
2. Put the whole size, e.g. **Month**, on the main *Project work* task, or share
   it across the project-work tasks by how big each is.
3. Leave small questions inside the project (*General & learning*) with their
   own small estimate.

A button to estimate a whole project at once is planned.

---

## A 10-minute weekly routine

1. Open the **DFTE** view for the current month.
2. Correct the **5 biggest tasks** by net hours: these move the number most.
3. Correct **3 small ones** at random: the AI tends to overestimate small tasks.
4. Check **purposes**: is a general question filed as project work? Is a tool
   setup filed as a project?
5. Tick **Not real work** on anything that shouldn't count.

After two or three weeks the learned factors settle, and you'll mostly only need
to check the biggest tasks.

---

## Good to know

- Corrections are saved in `~/.digital-fte/fte.db` and survive rescans and
  restarts. A task keeps the same identity across rebuilds, so your correction
  stays attached.
- Corrections never leave your Mac. The shared summary page shows only totals,
  and says how many tasks you reviewed.
- Rules and AI estimates are kept alongside your corrections. The headline's
  *without AI review* number and the trend chart's dotted line show the rules
  estimate, so the effect of the review is always visible.
