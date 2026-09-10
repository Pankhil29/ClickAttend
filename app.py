import streamlit as st
import io
import os
import hashlib
import sqlite3
import pandas as pd
import numpy as np
import cv2
from datetime import date, timedelta
from ocr_engine import extract_attendance_grid

# Folder where every original register photo gets archived (for later dispute/audit checks)
PHOTO_ARCHIVE_DIR = "register_photos"
os.makedirs(PHOTO_ARCHIVE_DIR, exist_ok=True)

# Database connection setup
conn = sqlite3.connect('attendance_system.db', check_same_thread=False)
c = conn.cursor()

# Table creation
def create_tables():
    # 1. Students table (Enrollment No is the primary key)
    c.execute('''CREATE TABLE IF NOT EXISTS students 
                 (enrollment_no TEXT PRIMARY KEY, 
                  roll_no INTEGER, 
                  name TEXT, 
                  branch TEXT, 
                  division TEXT,
                  mentor_name TEXT)''')
    
    # 2. Subjects table (auto-increment ID)
    c.execute('''CREATE TABLE IF NOT EXISTS subjects 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  sub_name TEXT)''')
    
    # 3. Attendance table (logs every lecture)
    c.execute('''CREATE TABLE IF NOT EXISTS attendance 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  enrollment_no TEXT, 
                  sub_name TEXT, 
                  date TEXT, 
                  status TEXT,
                  lecture_count INTEGER DEFAULT 1,
                  FOREIGN KEY(enrollment_no) REFERENCES students(enrollment_no))''')

    # 4. Photo uploads table (archive: original register photo for every OCR import,
    #    so it can be pulled back up later if attendance is disputed / needs audit)
    c.execute('''CREATE TABLE IF NOT EXISTS photo_uploads
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  branch TEXT,
                  division TEXT,
                  sub_name TEXT,
                  session_dates TEXT,
                  photo_path TEXT,
                  photo_hash TEXT,
                  uploaded_at TEXT)''')
    # Migration: older databases created before photo_hash existed won't have the
    # column -- add it if missing so upgrades don't break.
    try:
        c.execute("ALTER TABLE photo_uploads ADD COLUMN photo_hash TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists

    # 5. Divisions table -- declared divisions must persist even if a division
    #    currently has zero students imported into it. Relying on "DISTINCT division
    #    FROM students" silently drops any division with no rows yet.
    c.execute('''CREATE TABLE IF NOT EXISTS divisions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT UNIQUE)''')

    conn.commit()

create_tables()

# Divisions list comes from the persistent 'divisions' table (not from scanning
# students) so a division stays in every dropdown even before any student has
# been imported into it, and survives app restarts.
def get_division_options():
    existing = pd.read_sql("SELECT name FROM divisions ORDER BY name", conn)["name"].tolist()
    if not existing:
        # First run after upgrading: migrate whatever divisions already exist in
        # students data, so old data doesn't lose its divisions.
        legacy = pd.read_sql(
            "SELECT DISTINCT division FROM students WHERE division IS NOT NULL AND TRIM(division) != ''",
            conn
        )["division"].astype(str).tolist()
        for d in legacy:
            c.execute("INSERT OR IGNORE INTO divisions (name) VALUES (?)", (d.strip(),))
        conn.commit()
        existing = pd.read_sql("SELECT name FROM divisions ORDER BY name", conn)["name"].tolist()
    return existing

division_options = get_division_options()

# App title and icon
st.set_page_config(page_title="College Attendance System", layout="wide")

# Build the sidebar navigation
st.sidebar.title("Navigation")
page = st.sidebar.radio("Go to", ["Dashboard", "Mark Attendance", "Photo Attendance", "Correct Attendance", "View Reports", "Photo Archive", "Admin Panel"])

# Main area
if page == "Dashboard":
    # CSS fix: keep text color dark green/black so it stays visible against the background
    st.markdown("""
        <style>
        .stMetric {
            background-color: #EBF1DE !important;
            border: 2px solid #76933C !important;
            padding: 20px !important;
            border-radius: 10px !important;
        }
        /* Metric value (number) */
        [data-testid="stMetricValue"] {
            color: #2E7D32 !important;
            font-weight: bold !important;
        }
        /* Metric label (title) */
        [data-testid="stMetricLabel"] {
            color: #4F6228 !important;
            font-size: 16px !important;
            font-weight: bold !important;
        }
        </style>
    """, unsafe_allow_html=True)

    st.title("College Attendance Dashboard")
    
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Divisions", len(division_options))
    
    subjects_count = pd.read_sql("SELECT COUNT(*) as count FROM subjects", conn)['count'][0]
    m2.metric("Total Subjects", subjects_count)
    
    students_count = pd.read_sql("SELECT COUNT(*) as count FROM students", conn)['count'][0]
    m3.metric("Students Enrolled", students_count)
    m4.metric("Active Sessions", "Weekly")

    st.divider()
    st.subheader("Division-wise Strength")
    # LEFT JOIN from divisions (not students) so a division with 0 students imported
    # so far still shows up as a row with 0 -- it doesn't just vanish from the table.
    div_summary = pd.read_sql('''
        SELECT d.name as Division, COUNT(s.enrollment_no) as Total_Students
        FROM divisions d
        LEFT JOIN students s ON s.division = d.name
        GROUP BY d.name
        ORDER BY d.name
    ''', conn)
    st.table(div_summary)

elif page == "Mark Attendance":
    st.title("Mark Daily Attendance")
    
    # 1. Pull subjects from the database (so only what Admin has added shows up)
    subject_query = pd.read_sql("SELECT sub_name FROM subjects", conn)
    subject_list = subject_query['sub_name'].tolist()

    if not subject_list:
        st.warning("No subject has been added yet. Add at least one subject in Admin Panel before marking attendance.")
        st.stop()

    if not division_options:
        st.warning("No division has been added yet. Add at least one division in Admin Panel before marking attendance.")
        st.stop()

    col1, col2, col3 = st.columns(3)
    with col1:
        branch = st.selectbox("Branch", ["CSE", "CSE-AIML"])
    with col2:
        div = st.selectbox("Division", division_options)
    with col3:
        sub = st.selectbox("Select Subject", subject_list)

    col_d1, col_d2 = st.columns(2)
    with col_d1:
        date = st.date_input("Select Date")
    with col_d2:
        lec_count = st.number_input("No. of Lectures", min_value=1, max_value=4, value=1)

    # Pull students of this division from the database
    query = f"SELECT enrollment_no, name FROM students WHERE branch='{branch}' AND division='{div}'"
    df_students = pd.read_sql(query, conn)

    if not df_students.empty:
        st.write(f"### Marking for {branch} - {div}")
        
        # Multiselect for absentees
        absentees_info = st.multiselect(
            "Select Absent Students (Search by Enrollment or Name)", 
            options=df_students['enrollment_no'].astype(str) + " - " + df_students['name']
        )
        
        # Extract just the Enrollment No. from the selected list
        absent_enrollments = [info.split(" - ")[0] for info in absentees_info]

        if st.button("Save Attendance"):
            try:
                attendance_data = []
                for _, student in df_students.iterrows():
                    enroll = str(student['enrollment_no'])
                    # 'A' if the student is in the absent list, else 'P'
                    status = 'A' if enroll in absent_enrollments else 'P'
                    
                    attendance_data.append((
                        enroll, sub, str(date), status, lec_count
                    ))
                
                # Bulk insert into the database
                c.executemany('''INSERT INTO attendance (enrollment_no, sub_name, date, status, lecture_count) 
                                 VALUES (?, ?, ?, ?, ?)''', attendance_data)
                conn.commit()
                st.success(f"Successfully saved {sub} attendance for {len(df_students)} students!")
            except Exception as e:
                st.error(f"Error saving attendance: {e}")
    else:
        st.warning("No students found for this branch/division. Upload student data via Admin Panel first.")

elif page == "Photo Attendance":
    st.title("Photo-based Attendance Import")
    st.caption("Upload a photo of the attendance register; the system will detect the grid and read the absent marks. You can verify/correct the result before saving.")

    subject_query = pd.read_sql("SELECT sub_name FROM subjects", conn)
    subject_list = subject_query['sub_name'].tolist()

    if not subject_list:
        st.warning("No subject has been added yet. Add at least one subject in Admin Panel before marking attendance.")
        st.stop()

    if not division_options:
        st.warning("No division has been added yet. Add at least one division in Admin Panel before marking attendance.")
        st.stop()

    col1, col2, col3 = st.columns(3)
    with col1:
        p_branch = st.selectbox("Branch", ["CSE", "CSE-AIML"], key="p_branch")
    with col2:
        p_div = st.selectbox("Division", division_options, key="p_div")
    with col3:
        p_sub = st.selectbox("Select Subject", subject_list, key="p_sub")

    with st.expander("Detection Settings (adjust these if attendance looks wrong/shifted)"):
        info_columns = st.number_input(
            "How many info-columns appear BEFORE the first session column in the photo? (e.g. Roll No + Enrolment No + Name = 3)",
            min_value=1, max_value=8, value=3, step=1,
            help="Getting this wrong shifts every attendance column (S1 ends up reading the Enrolment/Name column instead) -- "
                 "look at the photo and count how many columns come before S1."
        )
        sensitivity = st.slider(
            "Mark sensitivity", min_value=20, max_value=150, value=60, step=5,
            help="Lower this if faint pen marks are being missed. Raise it if shadows/paper texture are being wrongly detected as marks."
        )

    uploaded_photo = st.file_uploader("Upload the attendance register photo", type=["jpg", "jpeg", "png"])

    if uploaded_photo is not None:
        file_bytes = np.asarray(bytearray(uploaded_photo.read()), dtype=np.uint8)
        photo_hash = hashlib.sha256(file_bytes.tobytes()).hexdigest()
        img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)

        # Safety check: has this EXACT photo already been saved before, under a
        # different branch/division? This catches the classic mistake of uploading
        # (say) D1's register photo but leaving the dropdown on D2.
        prior_use = pd.read_sql(
            "SELECT branch, division, sub_name, uploaded_at FROM photo_uploads WHERE photo_hash=? ORDER BY uploaded_at DESC LIMIT 1",
            conn, params=(photo_hash,)
        )
        mismatch_override = True
        if not prior_use.empty:
            prior = prior_use.iloc[0]
            if prior["branch"] != p_branch or prior["division"] != p_div:
                st.error(
                    f"This exact photo was already saved for **{prior['branch']} - {prior['division']}** "
                    f"({prior['sub_name']}, on {prior['uploaded_at']}). You currently have "
                    f"**{p_branch} - {p_div}** selected — this looks like a mismatch, not the right register for this division."
                )
                mismatch_override = st.checkbox(
                    "I understand this is unusual and confirm this photo really is for "
                    f"{p_branch} - {p_div} (e.g. two divisions share one physical register page).",
                    value=False, key="mismatch_override"
                )
                if not mismatch_override:
                    st.stop()

        try:
            result = extract_attendance_grid(img, sensitivity=sensitivity, info_columns=info_columns)
        except ValueError as e:
            st.error(f"{e}")
            st.stop()

        st.success(f"Grid detected: {result['num_students']} students x {result['num_sessions']} sessions")
        st.image(cv2.cvtColor(result["debug_image"], cv2.COLOR_BGR2RGB),
                  caption="Green = Present, Red = Absent (verify the grid was captured correctly)")

        # Quick sanity-check summary before trusting the table below
        total_cells = sum(len(r) for r in result["rows"])
        total_absent = sum(sum(r) for r in result["rows"])
        st.info(f"Detected: {total_cells - total_absent} Present marks, {total_absent} Absent marks (total {total_cells} cells). "
                f"If this number looks very different from your expectation, adjust the sensitivity slider above and try again.")

        # Students of this branch/division, ordered by roll_no (same order as photo rows)
        df_students = pd.read_sql(
            f"SELECT enrollment_no, roll_no, name FROM students WHERE branch='{p_branch}' AND division='{p_div}' ORDER BY roll_no ASC",
            conn
        )

        if df_students.empty:
            st.warning("No students found in the database for this division. Upload them via Admin Panel first.")
            st.stop()

        if len(df_students) != result["num_students"]:
            st.warning(
                f"Mismatch: the database has {len(df_students)} students in this division, "
                f"but {result['num_students']} rows were detected in the photo. "
                f"Matching is being done by roll-number order -- carefully verify the table below, "
                f"some students may have matched to the wrong row."
            )

        st.subheader("Session Dates")
        st.caption("Which date each column represents -- check/edit as needed (default: counted backward from today).")
        session_dates = []
        date_cols = st.columns(min(result["num_sessions"], 5))
        for idx in range(result["num_sessions"]):
            default_date = date.today() - timedelta(days=(result["num_sessions"] - 1 - idx))
            with date_cols[idx % len(date_cols)]:
                d = st.date_input(f"Session {idx+1}", value=default_date, key=f"sess_date_{idx}")
            session_dates.append(str(d))

        st.subheader("Verify / Correct Attendance")
        st.caption("The OCR result is in the table -- toggle the checkbox wherever it looks wrong, then Save.")

        n_rows = min(len(df_students), result["num_students"])
        preview_data = {"Roll No": [], "Name": [], "Enrollment": []}
        for idx in range(result["num_sessions"]):
            preview_data[f"S{idx+1} ({session_dates[idx]})"] = []

        for r in range(n_rows):
            preview_data["Roll No"].append(df_students.iloc[r]["roll_no"])
            preview_data["Name"].append(df_students.iloc[r]["name"])
            preview_data["Enrollment"].append(df_students.iloc[r]["enrollment_no"])
            for idx in range(result["num_sessions"]):
                is_absent = result["rows"][r][idx]
                preview_data[f"S{idx+1} ({session_dates[idx]})"].append(not is_absent)  # True = Present

        preview_df = pd.DataFrame(preview_data)

        edited_df = st.data_editor(
            preview_df,
            disabled=["Roll No", "Name", "Enrollment"],
            use_container_width=True,
            key="ocr_editor"
        )

        st.warning(f"Before saving, check the Name column above against the physical register photo -- confirm these really are the {p_branch} - {p_div} students.")
        typed_confirm = st.text_input(
            f"Type the division name exactly as shown ('{p_div}') to confirm you checked the names above match this photo:",
            key="typed_division_confirm"
        )
        confirm_match = typed_confirm.strip() == p_div
        if typed_confirm and not confirm_match:
            st.error(f"That doesn't match '{p_div}' — this won't unlock Save until it matches exactly. If you meant a different division, change the Division dropdown above first.")

        if st.button("Save This Attendance to Database", disabled=not confirm_match):
            try:
                attendance_data = []
                session_cols = [c for c in edited_df.columns if c.startswith("S")]
                for idx, sess_col in enumerate(session_cols):
                    the_date = session_dates[idx]
                    for _, row in edited_df.iterrows():
                        status = 'P' if row[sess_col] else 'A'
                        attendance_data.append((row["Enrollment"], p_sub, the_date, status, 1))

                c.executemany(
                    '''INSERT INTO attendance (enrollment_no, sub_name, date, status, lecture_count)
                       VALUES (?, ?, ?, ?, ?)''',
                    attendance_data
                )

                # Archive the original photo (exact bytes, not the resized/processed copy)
                # so it can be pulled back up later if a student disputes their attendance
                # or faculty asks to verify how a mark was read.
                import datetime as _dt
                ext = os.path.splitext(uploaded_photo.name)[1] or ".jpg"
                timestamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_sub = p_sub.replace(" ", "_")
                filename = f"{p_branch}_{p_div}_{safe_sub}_{session_dates[0]}_{timestamp}{ext}"
                photo_path = os.path.join(PHOTO_ARCHIVE_DIR, filename)
                with open(photo_path, "wb") as f:
                    f.write(file_bytes.tobytes())

                c.execute(
                    '''INSERT INTO photo_uploads (branch, division, sub_name, session_dates, photo_path, photo_hash, uploaded_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (p_branch, p_div, p_sub, ",".join(session_dates), photo_path, photo_hash, str(_dt.datetime.now()))
                )

                conn.commit()
                st.success(f"Saved! {len(session_cols)} sessions x {n_rows} students = {len(attendance_data)} entries added. "
                           f"The original photo has also been archived (for later verification).")
            except Exception as e:
                st.error(f"Error saving: {e}")

elif page == "Correct Attendance":
    st.title("Correct Attendance (Dispute Resolution)")
    st.caption("If a student believes their attendance was marked incorrectly, search here to fix it.")

    search_term = st.text_input("Search by student Roll No, Enrollment No, or Name")

    if search_term:
        query = f"""
            SELECT enrollment_no, roll_no, name FROM students
            WHERE enrollment_no LIKE '%{search_term}%'
               OR name LIKE '%{search_term}%'
               OR CAST(roll_no AS TEXT) LIKE '%{search_term}%'
        """
        matches = pd.read_sql(query, conn)

        if matches.empty:
            st.warning("No student found matching that name/roll/enrollment number.")
        else:
            student_options = (matches['enrollment_no'].astype(str) + " - " + matches['name']).tolist()
            selected = st.selectbox("Select student", student_options)
            sel_enrollment = selected.split(" - ")[0]

            records = pd.read_sql(
                f"SELECT id, sub_name, date, status, lecture_count FROM attendance WHERE enrollment_no='{sel_enrollment}' ORDER BY date DESC",
                conn
            )

            if records.empty:
                st.info("No attendance record found for this student.")
            else:
                st.write(f"### Attendance history for {selected}")
                edited_records = st.data_editor(
                    records,
                    disabled=["id", "sub_name", "date", "lecture_count"],
                    column_config={
                        "status": st.column_config.SelectboxColumn("Status", options=["P", "A"])
                    },
                    use_container_width=True,
                    key="correction_editor"
                )

                if st.button("Save Corrections"):
                    try:
                        for _, row in edited_records.iterrows():
                            c.execute(
                                "UPDATE attendance SET status=? WHERE id=?",
                                (row["status"], row["id"])
                            )
                        conn.commit()
                        st.success("Corrections saved!")
                    except Exception as e:
                        st.error(f"Error updating: {e}")

elif page == "Photo Archive":
    st.title("Photo Archive")
    st.caption("Every OCR import's original register photo is saved here -- come back and view it for disputes or verification.")

    archive_df = pd.read_sql(
        "SELECT id, branch, division, sub_name, session_dates, photo_path, uploaded_at FROM photo_uploads ORDER BY uploaded_at DESC",
        conn
    )

    if archive_df.empty:
        st.info("No photos archived yet. Whenever you save attendance from 'Photo Attendance', its original photo will appear here.")
    else:
        acol1, acol2, acol3 = st.columns(3)
        with acol1:
            f_branch = st.selectbox("Branch", ["All"] + sorted(archive_df["branch"].unique().tolist()))
        with acol2:
            f_div = st.selectbox("Division", ["All"] + sorted(archive_df["division"].unique().tolist()))
        with acol3:
            f_sub = st.selectbox("Subject", ["All"] + sorted(archive_df["sub_name"].unique().tolist()))

        filtered = archive_df.copy()
        if f_branch != "All":
            filtered = filtered[filtered["branch"] == f_branch]
        if f_div != "All":
            filtered = filtered[filtered["division"] == f_div]
        if f_sub != "All":
            filtered = filtered[filtered["sub_name"] == f_sub]

        st.write(f"### {len(filtered)} archived photo(s)")
        for _, rec in filtered.iterrows():
            with st.expander(f"{rec['sub_name']} — {rec['branch']} {rec['division']} — Sessions: {rec['session_dates']} — Uploaded: {rec['uploaded_at']}"):
                if os.path.exists(rec["photo_path"]):
                    st.image(rec["photo_path"], caption=os.path.basename(rec["photo_path"]))
                    with open(rec["photo_path"], "rb") as f:
                        st.download_button(
                            "Download Original Photo",
                            data=f.read(),
                            file_name=os.path.basename(rec["photo_path"]),
                            key=f"dl_{rec['id']}"
                        )
                else:
                    st.error("This photo file is missing from disk (it may have been deleted/moved).")

                confirm_key = f"confirm_delete_photo_{rec['id']}"
                if not st.session_state.get(confirm_key, False):
                    if st.button("Delete this photo", key=f"del_{rec['id']}"):
                        st.session_state[confirm_key] = True
                        st.rerun()
                else:
                    st.warning("This will permanently delete this photo and its archive record. This cannot be undone.")
                    dcol1, dcol2 = st.columns(2)
                    with dcol1:
                        if st.button("Yes, delete permanently", key=f"confirm_del_{rec['id']}", type="primary"):
                            if os.path.exists(rec["photo_path"]):
                                os.remove(rec["photo_path"])
                            c.execute("DELETE FROM photo_uploads WHERE id=?", (rec["id"],))
                            conn.commit()
                            st.session_state.pop(confirm_key, None)
                            st.success("Photo deleted.")
                            st.rerun()
                    with dcol2:
                        if st.button("Cancel", key=f"cancel_del_{rec['id']}"):
                            st.session_state.pop(confirm_key, None)
                            st.rerun()

elif page == "Admin Panel":
    st.title("Admin Panel")
    uploaded_file = st.file_uploader("Upload Excel", type=['xlsx'])
    
    if uploaded_file:
        st.subheader("Excel Import Settings")
        skip_top_rows = st.number_input(
            "1. How many rows to skip/remove from the top?",
            min_value=0,
            max_value=50,
            value=4,
            step=1,
            help="If the Excel has 4 rows of college letterhead like in the sample, the default of 4 is correct."
        )
        skip_bottom_rows = st.number_input(
            "2. How many rows to skip/remove from the bottom?",
            min_value=0,
            max_value=50,
            value=0,
            step=1,
            help="Use this to remove unwanted total/extra rows at the bottom."
        )
        division_count = st.number_input(
            "How many divisions are there?",
            min_value=1,
            max_value=50,
            value=max(1, len(division_options)),
            step=1,
            help="Set the number of divisions for this import."
        )
        division_names = []
        division_name_cols = st.columns(min(int(division_count), 4))
        for division_index in range(int(division_count)):
            with division_name_cols[division_index % len(division_name_cols)]:
                division_name = st.text_input(
                    f"Division {division_index + 1}",
                    value=division_options[division_index] if division_index < len(division_options) else f"D{division_index + 1}",
                    key=f"import_division_{division_index}"
                ).strip()
                division_names.append(division_name)

        reset_roll_numbers = st.checkbox("Reset roll numbers starting from 1", value=True)

        auto_distribute = st.checkbox(
            "Automatically split students evenly across the divisions above",
            value=True,
            help="When ON, the Division column in the Excel (if any) is ignored -- students are split into "
                 "as-equal-as-possible groups across however many divisions you declared above, in roll-number order "
                 "(e.g. 10 students / 3 divisions -> 3, 3, 4). Turn this OFF if your Excel already has a correct, "
                 "per-student Division column that you want to keep as-is."
        )

        df_upload = pd.read_excel(
            uploaded_file,
            skiprows=int(skip_top_rows),
            skipfooter=int(skip_bottom_rows)
        )
        
        # Clean up column names (strip whitespace, lowercase)
        # So 'Roll No' becomes 'roll_no' and 'Mentor' becomes 'mentor_name'
        # NOTE: if an Excel header cell is blank, pandas names that column with a
        # number (0, 1, 2...) instead of a string -- str() must run first, otherwise
        # .strip() crashes on an int.
        df_upload.columns = [str(c).strip().lower().replace(' ', '_') for c in df_upload.columns]
        
        # If a column is named just 'mentor', rename it to 'mentor_name'
        df_upload.rename(columns={
            'roll_no.': 'roll_no',
            'enrol._no.': 'enrollment_no',
            'enrol_no': 'enrollment_no',
            'enrollment_no.': 'enrollment_no',
            'mentor': 'mentor_name'
        }, inplace=True)

        st.write("3. Use the preview table to delete/edit any stray unwanted rows:")
        df_upload = st.data_editor(
            df_upload,
            num_rows="dynamic",
            use_container_width=True,
            key="student_import_editor"
        )
        
        if st.button("Import to Database"):
            try:
                # When auto-distributing, the Excel doesn't need its own Division column --
                # it gets computed below instead. Otherwise, Division is required from the file.
                required_columns = {'enrollment_no', 'roll_no', 'name', 'branch', 'mentor_name'}
                if not auto_distribute:
                    required_columns = required_columns | {'division'}

                missing_columns = required_columns - set(df_upload.columns)
                if missing_columns:
                    st.error(f"Required columns are missing: {', '.join(sorted(missing_columns))}")
                    st.stop()

                keep_cols = list(required_columns)
                df_upload = df_upload[keep_cols].dropna(subset=['enrollment_no', 'name'])

                division_names = [name for name in division_names if name]
                if len(division_names) != int(division_count) or len(set(division_names)) != len(division_names):
                    st.error("Division names must not be empty or duplicated.")
                    st.stop()

                if not auto_distribute:
                    imported_divisions = set(df_upload['division'].dropna().astype(str).str.strip())
                    unknown_divisions = imported_divisions - set(division_names)
                    if unknown_divisions:
                        st.error(f"These divisions in the Excel are not in the division list: {', '.join(sorted(unknown_divisions))}")
                        st.stop()

                if reset_roll_numbers:
                    df_upload['roll_no'] = range(1, len(df_upload) + 1)
                df_upload['enrollment_no'] = df_upload['enrollment_no'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                df_upload['roll_no'] = pd.to_numeric(df_upload['roll_no'], errors='coerce')
                df_upload = df_upload.dropna(subset=['roll_no'])
                df_upload['roll_no'] = df_upload['roll_no'].astype(int)

                if auto_distribute:
                    # Split students into as-equal-as-possible groups across the declared
                    # divisions, in roll-number order, with any remainder going to the LAST
                    # division(s) -- e.g. 10 students / 3 divisions -> sizes [3, 3, 4].
                    df_upload = df_upload.sort_values('roll_no').reset_index(drop=True)
                    n = len(df_upload)
                    k = len(division_names)
                    base, remainder = divmod(n, k)
                    sizes = [base] * k
                    for i in range(remainder):
                        sizes[k - 1 - i] += 1

                    division_column = []
                    for div_name, size in zip(division_names, sizes):
                        division_column.extend([div_name] * size)
                    df_upload['division'] = division_column

                # Using if_exists='replace' so old dummy/sample data is cleared out
                df_upload.to_sql('students', conn, if_exists='replace', index=False)

                # Save declared divisions PERMANENTLY -- so even a division with 0 students
                # right now still shows up everywhere (Mark Attendance, Reports, etc.)
                for d in division_names:
                    c.execute("INSERT OR IGNORE INTO divisions (name) VALUES (?)", (d,))
                conn.commit()

                if auto_distribute:
                    counts_str = ", ".join(f"{d}: {s}" for d, s in zip(division_names, sizes))
                    st.success(f"Data imported! Students auto-distributed as -> {counts_str}")
                else:
                    st.success("Data imported successfully!")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")
        
    st.divider()
    st.subheader("Division Management")
    st.caption("You can also add/remove divisions here directly, without an Excel import.")

    current_divs = pd.read_sql("SELECT id, name FROM divisions ORDER BY name", conn)
    if not current_divs.empty:
        st.write("Current Divisions:", ", ".join(current_divs["name"].tolist()))

    dcol1, dcol2 = st.columns([2, 1])
    with dcol1:
        new_div = st.text_input("Enter New Division Name (e.g., D7)")
    with dcol2:
        if st.button("Add Division"):
            if new_div.strip():
                try:
                    c.execute("INSERT INTO divisions (name) VALUES (?)", (new_div.strip(),))
                    conn.commit()
                    st.success(f"Division '{new_div.strip()}' added!")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.warning("This division is already in the list!")
            else:
                st.error("Please enter a division name!")

    if not current_divs.empty:
        remove_div = st.selectbox("Remove a division", ["-- Select --"] + current_divs["name"].tolist(), key="remove_div_select")
        if remove_div != "-- Select --":
            div_student_count = pd.read_sql(
                "SELECT COUNT(*) as cnt FROM students WHERE division=?", conn, params=(remove_div,)
            )["cnt"][0]
            if div_student_count > 0:
                st.warning(f"'{remove_div}' has {div_student_count} students -- remove or move them to another division first before this division can be removed.")
            else:
                if st.button(f"Confirm Remove '{remove_div}'"):
                    c.execute("DELETE FROM divisions WHERE name=?", (remove_div,))
                    conn.commit()
                    st.success(f"Division '{remove_div}' removed.")
                    st.rerun()
    st.subheader("Subject Management")

    # Show existing subjects
    existing_subs = pd.read_sql("SELECT DISTINCT sub_name FROM subjects", conn)
    if not existing_subs.empty:
        st.write("Current Subjects:", ", ".join(existing_subs['sub_name'].tolist()))

    # Form to add a new subject
    col_sub1, col_sub2 = st.columns([2, 1])
    with col_sub1:
        new_sub = st.text_input("Enter New Subject Name (e.g., TOC, CNS)")
    with col_sub2:
        if st.button("Add Subject"):
            if new_sub:
                try:
                    # 1. Make sure the table exists first (safety check)
                    c.execute('''CREATE TABLE IF NOT EXISTS subjects 
                                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                                  sub_name TEXT UNIQUE)''')
                    
                    # 2. The actual command that saves the data
                    c.execute("INSERT INTO subjects (sub_name) VALUES (?)", (new_sub.strip().upper(),))
                    
                    conn.commit()
                    st.success(f"Subject '{new_sub.upper()}' successfully added!")
                    st.rerun() 
                except sqlite3.IntegrityError:
                    st.warning("This subject is already in the list!")
                except Exception as e:
                    st.error(f"Error: {e}")
            else:
                st.error("Please enter a subject name!")
    st.divider()
    st.subheader("Danger Zone")
    if st.session_state.pop("system_data_deleted", False):
        st.success("All students, subjects, divisions, attendance, and archived photos have been deleted. You can now start fresh.")

    st.error("DANGER: This option will permanently delete ALL students, subjects, divisions, attendance, AND archived photos.")
    if "confirm_delete_all_data" not in st.session_state:
        st.session_state.confirm_delete_all_data = False

    if not st.session_state.confirm_delete_all_data:
        if st.button("DELETE ENTIRE SYSTEM DATA", type="primary", key="delete_entire_system_data"):
            st.session_state.confirm_delete_all_data = True
            st.rerun()
    else:
        st.error("Final confirmation: do you want to permanently delete ALL student, subject, division, attendance, and photo records? Divisions will not reappear until you add them again.")
        confirm_col, cancel_col = st.columns(2)
        with confirm_col:
            if st.button("YES, DELETE EVERYTHING", type="primary", key="confirm_delete_everything"):
                try:
                    # Delete archived photo FILES from disk first, before clearing the DB records that point to them
                    photo_paths = pd.read_sql("SELECT photo_path FROM photo_uploads", conn)["photo_path"].tolist()
                    for p in photo_paths:
                        if p and os.path.exists(p):
                            os.remove(p)

                    conn.execute("DELETE FROM attendance")
                    conn.execute("DELETE FROM students")
                    conn.execute("DELETE FROM subjects")
                    conn.execute("DELETE FROM divisions")
                    conn.execute("DELETE FROM photo_uploads")
                    conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('attendance', 'students', 'subjects', 'divisions', 'photo_uploads')")
                    conn.commit()
                    remaining_data = conn.execute(
                        "SELECT "
                        "(SELECT COUNT(*) FROM students) + "
                        "(SELECT COUNT(*) FROM subjects) + "
                        "(SELECT COUNT(*) FROM divisions) + "
                        "(SELECT COUNT(*) FROM photo_uploads) + "
                        "(SELECT COUNT(*) FROM attendance)"
                    ).fetchone()[0]

                    if remaining_data == 0:
                        st.session_state.confirm_delete_all_data = False
                        st.session_state.system_data_deleted = True
                        st.rerun()
                    else:
                        st.error("System data was not fully deleted. Please try again.")
                except sqlite3.Error as e:
                    conn.rollback()
                    st.session_state.confirm_delete_all_data = False
                    st.error(f"Error while deleting system data: {e}")
        with cancel_col:
            if st.button("CANCEL", key="cancel_delete_everything"):
                st.session_state.confirm_delete_all_data = False
                st.rerun()

    if st.button("Clear All Attendance Data"):
        try:
            conn.execute("DELETE FROM attendance")
            conn.commit()
            remaining_records = conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]

            if remaining_records == 0:
                st.success("All attendance data has been cleared successfully.")
                st.rerun()
            else:
                st.error("Attendance data was not cleared. Please try again.")
        except sqlite3.Error as e:
            conn.rollback()
            st.error(f"Error while clearing attendance: {e}")

elif page == "View Reports":
    st.title("Final Attendance Report")

    if not division_options:
        st.warning("No division has been added yet. Add at least one division in Admin Panel to see reports.")
        st.stop()

    tab1, tab2 = st.tabs(["Overall Semester Report", "Weekly Day-wise Report"])

    # ============================================================
    # TAB 1: OVERALL SEMESTER REPORT (subject-wise, with eligibility)
    # ============================================================
    with tab1:
        col1, col2, col3 = st.columns(3)
        with col1:
            selected_branch = st.selectbox("Select Branch", ["CSE", "CSE-AIML"], key="ov_branch")
        with col2:
            selected_div = st.selectbox("Select Division", division_options, key="ov_div")
        with col3:
            eligibility_threshold = st.number_input("Min. Attendance % Required (Exam Eligibility)", min_value=0, max_value=100, value=50, key="ov_threshold")

        # 1. Master student data
        query_stu = f"SELECT roll_no, enrollment_no, name, mentor_name FROM students WHERE branch='{selected_branch}' AND division='{selected_div}' ORDER BY roll_no ASC"
        df_main = pd.read_sql(query_stu, conn)

        # Data type fix
        df_main['enrollment_no'] = df_main['enrollment_no'].astype(str)

        if not df_main.empty:
            query_att = "SELECT enrollment_no, sub_name, status, lecture_count FROM attendance"
            df_att = pd.read_sql(query_att, conn)
            df_att['enrollment_no'] = df_att['enrollment_no'].astype(str)

            subjects_query = pd.read_sql("SELECT DISTINCT sub_name FROM subjects", conn)
            subjects = subjects_query['sub_name'].tolist() if not subjects_query.empty else []

            if not subjects:
                st.info("No subject has been added yet, so there is no attendance data to calculate. Add a subject in Admin Panel and mark some attendance first.")

            for sub in subjects:
                sub_att = df_att[(df_att['sub_name'] == sub)]
                div_stu_enrolls = df_main['enrollment_no'].tolist()
                sub_att_div = sub_att[sub_att['enrollment_no'].isin(div_stu_enrolls)]

                if not sub_att_div.empty:
                    total_conducted = sub_att_div.groupby('enrollment_no')['lecture_count'].sum().max()
                    df_present = sub_att_div[sub_att_div['status'] == 'P'].groupby('enrollment_no')['lecture_count'].sum().reset_index()
                    df_present.columns = ['enrollment_no', f'{sub}_Attended']
                    df_present['enrollment_no'] = df_present['enrollment_no'].astype(str)

                    df_main = pd.merge(df_main, df_present, on='enrollment_no', how='left').fillna(0)
                    df_main[f'{sub}_Conducted'] = total_conducted
                    df_main[f'{sub}_%'] = (df_main[f'{sub}_Attended'] / df_main[f'{sub}_Conducted'] * 100).round(2).fillna(0)
                else:
                    # If there's no data, set 0 so the column sequence doesn't break
                    df_main[f'{sub}_Conducted'] = 0
                    df_main[f'{sub}_Attended'] = 0
                    df_main[f'{sub}_%'] = 0

            # 3. Overall calculation
            att_cols = [c for c in df_main.columns if '_Attended' in c]
            cond_cols = [c for c in df_main.columns if '_Conducted' in c]

            if att_cols:
                df_main['Overall_Attended'] = df_main[att_cols].sum(axis=1)
                df_main['Overall_Conducted'] = df_main[cond_cols].sum(axis=1)
                df_main['Overall_%'] = (df_main['Overall_Attended'] / df_main['Overall_Conducted'] * 100).round(2).fillna(0)

            # --- Fixing the column sequence here ---
            # Correct order, matching the reference layout
            ordered_cols = ['roll_no', 'enrollment_no', 'name']

            for sub in subjects:
                ordered_cols.extend([f'{sub}_Conducted', f'{sub}_Attended', f'{sub}_%'])

            ordered_cols.extend(['Overall_Conducted', 'Overall_Attended', 'Overall_%', 'mentor_name'])

            final_ordered_cols = [c for c in ordered_cols if c in df_main.columns]
            df_display = df_main[final_ordered_cols]

            # 4. Display on dashboard
            st.write(f"### {selected_branch} - {selected_div} Attendance Report")
            st.caption(f"Red = below {eligibility_threshold}% (exam eligibility criteria not met — faculty/mentor should be notified)")

            def highlight_ineligible(row):
                styles = [''] * len(row)
                if 'Overall_%' in row.index and row['Overall_%'] < eligibility_threshold:
                    styles = ['background-color: #FFCDD2'] * len(row)
                return styles

            styled = df_display.style.background_gradient(
                cmap='Greens', subset=[c for c in df_display.columns if '%' in c and c != 'Overall_%']
            ).apply(highlight_ineligible, axis=1)

            st.dataframe(styled, use_container_width=True)

            # Quick summary of at-risk students
            at_risk = df_main[df_main['Overall_%'] < eligibility_threshold] if 'Overall_%' in df_main.columns else pd.DataFrame()
            if not at_risk.empty:
                st.warning(f"{len(at_risk)} student(s) below {eligibility_threshold}% — exam eligibility at risk:")
                st.dataframe(at_risk[['roll_no', 'name', 'Overall_%', 'mentor_name']], use_container_width=True)

            # --- Export section (green Excel) ---
            st.divider()
            st.subheader("Export Official Report")

            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df_display.to_excel(writer, sheet_name='Attendance', startrow=2, index=False, header=False)

                workbook = writer.book
                worksheet = writer.sheets['Attendance']

                header_fmt = workbook.add_format({'bold': True, 'align': 'center', 'valign': 'vcenter', 'fg_color': '#D7E4BC', 'border': 1})
                sub_header_fmt = workbook.add_format({'bold': True, 'align': 'center', 'valign': 'vcenter', 'rotation': 90, 'fg_color': '#EBF1DE', 'border': 1, 'font_size': 9})

                worksheet.merge_range('A1:C1', 'Student Information', header_fmt)
                for i, h in enumerate(['Roll No', 'Enrollment No', 'Student Name']):
                    worksheet.write(1, i, h, header_fmt)

                col_idx = 3
                for sub in subjects:
                    worksheet.merge_range(0, col_idx, 0, col_idx + 2, sub, header_fmt)
                    worksheet.write(1, col_idx, 'Conducted Lec', sub_header_fmt)
                    worksheet.write(1, col_idx + 1, 'Attended Lec', sub_header_fmt)
                    worksheet.write(1, col_idx + 2, 'Percentage (%)', sub_header_fmt)
                    col_idx += 3

                worksheet.merge_range(0, col_idx, 0, col_idx + 2, 'Overall Attendance', header_fmt)
                worksheet.write(1, col_idx, 'Total Cond.', sub_header_fmt)
                worksheet.write(1, col_idx + 1, 'Total Att.', sub_header_fmt)
                worksheet.write(1, col_idx + 2, 'Overall %', sub_header_fmt)
                worksheet.write(0, col_idx + 3, 'Mentor', header_fmt)
                worksheet.write(1, col_idx + 3, 'Mentor Name', header_fmt)

                worksheet.set_column('C:C', 35)

            st.download_button("Download Official Excel (Green)", data=output.getvalue(), file_name=f"Report_{selected_div}.xlsx")
        else:
            st.warning("No data found for this division. Upload it via Admin Panel.")

    # ============================================================
    # TAB 2: WEEKLY DAY-WISE REPORT (Mon-Sat breakdown for a chosen week)
    # ============================================================
    with tab2:
        st.caption("Mon-Sat breakdown for any chosen week — a student can see exactly which day they attended and which day they didn't.")

        wcol1, wcol2, wcol3, wcol4 = st.columns(4)
        with wcol1:
            w_branch = st.selectbox("Branch", ["CSE", "CSE-AIML"], key="w_branch")
        with wcol2:
            w_div = st.selectbox("Division", division_options, key="w_div")
        with wcol3:
            week_start = st.date_input("Week Start (Monday)", value=date.today() - timedelta(days=date.today().weekday() + 7), key="week_start")
        with wcol4:
            week_end = st.date_input("Week End (Saturday)", value=week_start + timedelta(days=5), key="week_end")

        w_students = pd.read_sql(
            f"SELECT roll_no, enrollment_no, name, mentor_name FROM students WHERE branch='{w_branch}' AND division='{w_div}' ORDER BY roll_no ASC",
            conn
        )
        w_students['enrollment_no'] = w_students['enrollment_no'].astype(str)

        if w_students.empty:
            st.warning("No data found for this division.")
        else:
            w_att = pd.read_sql("SELECT enrollment_no, date, status, lecture_count FROM attendance", conn)
            w_att['enrollment_no'] = w_att['enrollment_no'].astype(str)
            w_att['date_parsed'] = pd.to_datetime(w_att['date'], errors='coerce')

            mask = (w_att['date_parsed'] >= pd.Timestamp(week_start)) & (w_att['date_parsed'] <= pd.Timestamp(week_end))
            week_att = w_att[mask].copy()
            week_att['day_name'] = week_att['date_parsed'].dt.day_name()

            day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
            result_rows = []

            for _, stu in w_students.iterrows():
                stu_att = week_att[week_att['enrollment_no'] == stu['enrollment_no']]
                row = {
                    'Roll No': stu['roll_no'],
                    'Enrollment No': stu['enrollment_no'],
                    'Name': stu['name'],
                    'Mentor': stu['mentor_name'],
                }
                total_attended, total_conducted = 0, 0
                for day in day_order:
                    day_att = stu_att[stu_att['day_name'] == day]
                    conducted_that_day = day_att['lecture_count'].sum()
                    attended_that_day = day_att[day_att['status'] == 'P']['lecture_count'].sum()
                    row[day[:3]] = int(attended_that_day)
                    total_attended += attended_that_day
                    total_conducted += conducted_that_day
                row['Total Attended'] = int(total_attended)
                row['Total Not Attended'] = int(total_conducted - total_attended)
                row['Average %'] = round((total_attended / total_conducted * 100), 2) if total_conducted > 0 else 0.0
                result_rows.append(row)

            week_df = pd.DataFrame(result_rows)
            st.write(f"### Week: {week_start} to {week_end} — {w_branch} {w_div}")

            def highlight_week_risk(row):
                if row['Average %'] < 50:
                    return ['background-color: #FFCDD2'] * len(row)
                return [''] * len(row)

            st.dataframe(
                week_df.style.apply(highlight_week_risk, axis=1),
                use_container_width=True
            )

            week_output = io.BytesIO()
            with pd.ExcelWriter(week_output, engine='xlsxwriter') as writer:
                week_df.to_excel(writer, sheet_name='WeeklyReport', index=False)
            st.download_button(
                "Download Weekly Report (Excel)",
                data=week_output.getvalue(),
                file_name=f"Weekly_{w_div}_{week_start}_to_{week_end}.xlsx"
            )