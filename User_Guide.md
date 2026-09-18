# Complete User Guide — Smart Attendance System

This guide walks through everything, in order, starting from a completely empty database (exactly the state you're in right now after deleting all data). Follow it top to bottom for a full working demo.

Before starting this guide, make sure the app is already running — see `HOW_TO_RUN.md` for that part (extracting the ZIP, installing requirements, `streamlit run app.py`). This guide picks up from there, once the browser tab is open.

---

## Step 1: Add Divisions (Admin Panel)

1. In the left sidebar, click **Admin Panel**.
2. Scroll down to the **Division Management** section (you don't need to upload an Excel file for this step).
3. In the **"Enter New Division Name"** box, type a division name — e.g. `D1` — and click **Add Division**.
4. Repeat for as many divisions as you need (e.g. `D2`, `D3`...).
5. You'll see a top-right toast notification each time, and the "Current Divisions" list at the top of this section will update.

> You can skip this step and instead declare divisions during the Excel import in Step 2 — both work. Doing it here first is useful if you want to add divisions before you have a roster ready.

---

## Step 2: Add Students (Admin Panel → Excel Import)

1. Still in **Admin Panel**, scroll to the top and click **"Browse files"** under **"Upload Excel"**.
2. Select a student roster Excel file (use `sample_data/sample_roster.xlsx` if you just want to test, or your real roster).
3. Set **"How many rows to skip/remove from the top?"** — this should match how many header/letterhead rows are above the actual column headers (Roll No, Name, etc.) in your Excel. For `sample_data/sample_roster.xlsx`, use **4**.
4. Set **"How many divisions are there?"** and type each division's name in the boxes that appear (these must match what you added in Step 1, or you can declare fresh ones here).
5. **"Automatically split students evenly across the divisions above"** — leave this checked if your Excel does NOT have a reliable per-student Division column and you just want students split evenly. Uncheck it if your Excel already has a correct Division column per student that you want to keep exactly as-is.
6. Review the preview table that appears — delete any stray junk rows here if needed.
7. Click **Import to Database**. You'll see a toast confirming how many students were imported (and how they were split across divisions, if auto-distribute was on).

---

## Step 3: Add a Subject (Admin Panel)

1. Still in **Admin Panel**, scroll to **Subject Management**.
2. Type a subject name (e.g. `TOC`) in **"Enter New Subject Name"** and click **Add Subject**.
3. Repeat for every subject you want to track attendance for.

> This step is required. You cannot mark attendance for any division until at least one subject exists — this is deliberate, so attendance never gets saved against a missing/fake subject.

---

## Step 4: Check the Dashboard

Click **Dashboard** in the sidebar. You should now see:

- **Total Divisions** — however many you added in Step 1/2.
- **Total Subjects** — however many you added in Step 3.
- **Students Enrolled** — total students imported in Step 2.
- **Division-wise Strength** — a table showing how many students are in each division (divisions with 0 students still show, with a 0).

---

## Step 5: Mark Attendance Manually

1. Click **Mark Attendance** in the sidebar.
2. Select **Branch**, **Division**, and **Subject** from the dropdowns.
3. Pick the **Date** and **No. of Lectures** for that session.
4. In **"Select Absent Students"**, search and pick only the students who were absent (everyone else is automatically marked Present).
5. Click **Save Attendance**. A toast confirms it's saved.

---

## Step 6: Mark Attendance from a Photo

1. Click **Photo Attendance** in the sidebar.
2. Select **Branch**, **Division**, and **Subject**.
3. Open **"Detection Settings"** and check:
   - **info_columns**: how many columns come before the first attendance column in your photo (e.g. Roll No + Enrolment No + Name = 3). Use **3** for `sample_data/sample_register_photo.png`.
   - **sensitivity**: leave at the default (60) unless marks are being missed or over-detected.
4. Click **"Upload the attendance register photo"** and choose a photo (use `sample_data/sample_register_photo.png` to test, or take a real photo of a paper register — make sure it's well-lit, straight-on, and the full table is in frame).
5. The app shows the detected grid with **green boxes = Present** and **red boxes = Absent** — check this against the real register.
6. Check/edit the **Session Dates** for each column if needed.
7. In the **Verify / Correct Attendance** table, toggle any checkbox that looks wrong.
8. **Important**: before saving, type the division name exactly (e.g. `D1`) in the confirmation box — this unlocks the Save button. This step exists to prevent accidentally saving one division's photo under a different division.
9. Click **Save This Attendance to Database**. A toast confirms it, and the original photo is archived automatically (viewable later under **Photo Archive** in the sidebar).

---

## Step 7: View and Download Reports

1. Click **View Reports** in the sidebar.
2. **Overall Semester Report** tab:
   - Pick Branch, Division, and the minimum attendance % required for exam eligibility.
   - You'll see a table with each student's attendance % per subject and overall, with students below the threshold highlighted red.
   - Click **Download Official Excel (Green)** to get a formatted Excel file.
3. **Weekly Day-wise Report** tab:
   - Pick Branch, Division, and a week's start/end date.
   - You'll see a Monday–Saturday breakdown of who attended which day.
   - Click **Download Weekly Report (Excel)** to get that as an Excel file.

---

## Quick Reference: Page-by-Page Summary

| Page               | What it's for                                                    |
| ------------------ | ---------------------------------------------------------------- |
| Dashboard          | Overview: counts and division-wise strength                      |
| Mark Attendance    | Manually mark daily attendance                                   |
| Photo Attendance   | Upload a register photo, auto-detect attendance                  |
| Correct Attendance | Search a student and fix a wrong attendance entry                |
| View Reports       | Semester % and weekly breakdowns, downloadable as Excel          |
| Photo Archive      | View/download/delete previously uploaded register photos         |
| Admin Panel        | Add students (Excel), divisions, subjects; danger-zone data wipe |

---

## If You Want to Start Over

In **Admin Panel → Danger Zone**:

- **"Clear All Attendance Data"** — wipes only attendance records, keeps students/subjects/divisions.
- **"DELETE ENTIRE SYSTEM DATA"** — wipes everything (students, subjects, divisions, attendance, and archived photos). You'll be asked to confirm before it actually deletes anything.
