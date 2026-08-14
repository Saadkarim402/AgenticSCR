# AgenticSCR Concepts & Architectural Decisions

This document logs core concepts, detailed explanations, and architectural decisions discussed during development.

---

## The Git Staging Trick (Phase 2 Webhook)

### The Problem
The core `AgenticSCR` AI pipeline (Detector and Validator) was designed to run locally on a developer's machine by reading `git diff --staged` (files that are `git add`ed but not yet committed). However, Pull Requests on GitHub do not have a staging area. A PR is a comparison between two finished commits (`head_sha` and `base_sha`). If we rewrote the `Detector` to understand PRs, we would violate the architectural constraint to treat the core pipeline as a stable, untouchable library.

### The Solution: Tricking Git
We can manually shift Git's pointers to make it think the PR's differences are just staged, uncommitted changes. 

To understand how, we must look at Git's three "trees":
1. **The Working Directory**: The actual files on your hard drive.
2. **The Staging Area (Index)**: The intermediate area holding differences ready to be committed.
3. **HEAD (The Commit History)**: Git's pointer to the "current" committed state.

### Step-by-Step Execution

#### Step 1: `git checkout <head_sha> --detach`
When we run this, Git updates two of its three areas:
1. **HEAD:** Moves to `head_sha` (the new code in the PR).
2. **Working Directory:** Updates the files on disk to match `head_sha` (the new code).
3. **Staging Area:** Empty, because the working directory perfectly matches HEAD.

#### Step 2: `git reset --soft <base_sha>`
The `--soft` flag is the magic. A normal reset (`--hard`) would wipe out the files on disk. `--soft` tells Git to **only move the HEAD pointer** and leave the Staging Area and Working Directory untouched.

1. **HEAD:** Moves backwards to `base_sha` (the old code in the main branch). Git now believes the current committed state of the project is the old code.
2. **Working Directory:** Untouched. The files on disk still contain the new code (`head_sha`).
3. **Staging Area:** Because HEAD just changed, but the files on disk didn't, Git automatically calculates the difference between the two and puts that difference in the Staging Area.

### The Result
Because the Staging Area now contains the exact differences between the feature branch and the main branch, running `git diff --staged` outputs the exact PR diff. We have perfectly spoon-fed the Pull Request changes directly into the AI pipeline without modifying the pipeline itself.
