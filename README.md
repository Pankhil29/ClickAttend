# Smart Attendance System

A Streamlit-based college attendance management system for CSE department, supporting manual attendance marking, photo/OCR-based bulk attendance import, dispute correction, and semester/weekly reports.

## Features

- **Dashboard** — quick overview: total divisions, subjects, students enrolled, and division-wise strength.
- **Mark Attendance** — manually mark daily attendance for a branch/division/subject by selecting absentees.
- **Photo Attendance** — upload a photo of a paper attendance register; the system detects the grid and reads pen marks (any color) to determine who's absent, with a review/correction step before saving.
- **Correct Attendance** — search any student and fix a wrongly-marked attendance record (dispute resolution).
- **View Reports** — two report types:
  - _Overall Semester Report_: subject-wise and overall attendance % per student, with an exam-eligibility threshold and a downloadable formatted Excel export.
  - _Weekly Day-wise Report_: Mon–Sat breakdown for any chosen week, downloadable as Excel.
- **Photo Archive** — every register photo used for a Photo Attendance import is archived (original file, not just the processed pixels), searchable by branch/division/subject, viewable and downloadable, and deletable.
- **Admin Panel** —
  - Import students from an Excel roster (with configurable header-row skipping).
  - **Auto-distribute students evenly across divisions** (toggleable) — splits the imported roster into as-equal-as-possible groups across however many divisions you declare, in roll-number order. Turn this off if your Excel already has a correct per-student Division column you want to keep.
  - Add/remove divisions and subjects directly (without a re-import).
  - Danger Zone: clear all attendance only, or wipe the entire system (students, subjects, divisions, attendance, and archived photos) with a confirmation step.

Toast notifications (top-right) confirm every save/add/delete action as it happens.

## Project Structure

```
.
├── app.py                  # Streamlit UI — all pages/routes live here
├── ocr_engine.py            # Photo → attendance-grid extraction logic (OpenCV)
├── attendance_system.db     # SQLite database (created automatically on first run)
└── register_photos/         # Archived original register photos (created automatically)
```

## Setup

1. Install dependencies:
   ```bash
   pip install streamlit pandas numpy opencv-python-headless xlsxwriter openpyxl
   ```
2. Run the app:
   ```bash
   streamlit run app.py
   ```
3. The app opens in your browser (default `http://localhost:8501`). A SQLite database file (`attendance_system.db`) and a `register_photos/` folder are created automatically in the same directory on first run.

## Recommended First-Time Setup Order

1. **Admin Panel → Excel Import Settings**: upload your student roster Excel, set how many rows to skip at the top (to skip college letterhead rows), declare your divisions, and import. (Decide whether to use auto-distribute or keep your Excel's own Division column.)
2. **Admin Panel → Subject Management**: add at least one subject. _Attendance cannot be marked for any division/subject until a subject exists_ — this is enforced deliberately, so attendance never gets saved against a placeholder/fake subject.
3. Now use **Mark Attendance** or **Photo Attendance** to start recording attendance, and **View Reports** to see the results.

## Photo Attendance — How It Works

The OCR does **not** read student names from the photo — it only detects the grid lines and reads whether each cell contains a colored pen mark (any color, not just blue), then matches rows to students **by roll-number order** in the database. Because of this:

- The **Division** and **Subject** dropdowns you select before uploading are trusted as correct — the system cannot independently verify "this photo is really Division X" from the image content.
- Before saving, you must **type the division name exactly** as a confirmation step (this catches the classic mistake of uploading one division's photo while a different division is still selected in the dropdown).
- If the number of rows detected in the photo doesn't match the number of students in the selected division, you'll see a mismatch warning — review the preview table carefully in that case.
- **Detection Settings** (in an expander on the page):
  - `info_columns`: how many non-session columns (Roll No, Enrolment No, Name, etc.) appear before the first attendance column in your register photo. Getting this wrong shifts every attendance column — set it to match your actual register layout.
  - `sensitivity`: how strong a pen mark's color needs to be to count as a mark. Lower it if faint marks are being missed; raise it if shadows/paper texture are being misread as marks.

## Known Limitations

- **SQLite + local disk storage**: the database and archived photos live on local disk next to `app.py`. This is fine for local/demo use, but if deployed to a platform with ephemeral storage (e.g. some free cloud hosts), data will not persist across restarts.
- **Admin import uses `if_exists='replace'`** for the students table, which means the `enrollment_no` primary-key constraint is not preserved across re-imports (a fresh table is created each time). Not an issue for normal use, but worth knowing if extending the schema later.
- **No real text-OCR of names**: see "Photo Attendance — How It Works" above. Division/Subject correctness before saving is the user's responsibility, with the typed-confirmation step as a safety net.

## Tech Stack

- [Streamlit](https://streamlit.io/) — UI
- [OpenCV](https://opencv.org/) — grid detection and mark detection for Photo Attendance
- [pandas](https://pandas.pydata.org/) — data wrangling and reporting
- SQLite — storage
- [XlsxWriter](https://xlsxwriter.readthedocs.io/) — formatted Excel report export
