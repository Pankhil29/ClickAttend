"""
ocr_engine.py
--------------
Attendance-sheet OCR logic: detects the grid on a photographed attendance
register (rows = students, columns = lecture sessions), and flags a cell as
ABSENT if it contains a colored mark/dot (matching the college's marking
convention: a pen mark = absent, blank cell = present).

Kept separate from app.py so the UI (Streamlit) and the image-processing logic
don't get tangled -- easier to test and debug independently.
"""

import cv2
import numpy as np


def _detect_grid_points(img):
    """Find all grid line intersections in the image and cluster them into rows."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    thresh = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY_INV,
        15, 2
    )

    vertical = cv2.morphologyEx(
        thresh, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, 25))
    )
    horizontal = cv2.morphologyEx(
        thresh, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (25, 1))
    )

    intersections = cv2.bitwise_and(vertical, horizontal)
    cnts, _ = cv2.findContours(intersections, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    points = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        points.append((x + w // 2, y + h // 2))

    if not points:
        return []

    def cluster(values, tolerance):
        clusters = []
        for value in sorted(values):
            if not clusters or value - clusters[-1][-1] > tolerance:
                clusters.append([value])
            else:
                clusters[-1].append(value)
        return [round(sum(group) / len(group)) for group in clusters]

    # A faint line can cause one intersection contour to disappear. Rebuild the
    # rectangular grid from shared X/Y coordinates instead of trusting each row's
    # individual contour count.
    x_coords = cluster([x for x, _ in points], tolerance=8)
    y_coords = cluster([y for _, y in points], tolerance=20)
    return [[(x, y) for x in x_coords] for y in y_coords]


def _is_absent(cell, saturation_threshold=60, value_threshold=40, min_area_ratio=0.03):
    """
    A cell is marked ABSENT if it contains a colored pen mark (dot/tick/cross,
    any color -- blue, black, red, whatever the college uses).

    Instead of hardcoding one specific blue shade, this looks for pixels that
    are clearly "ink" rather than "paper" or "grid line":
      - white paper background  -> low saturation, high brightness
      - black/gray grid lines   -> low saturation, low-to-mid brightness
      - a pen mark of any color -> noticeably higher saturation than both

    This makes detection work across different pens, cameras and lighting,
    instead of only the one blue shade that was hardcoded before.
    """
    if cell.size == 0:
        return False

    hsv = cv2.cvtColor(cell, cv2.COLOR_BGR2HSV)
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]

    mask = ((s > saturation_threshold) & (v > value_threshold)).astype(np.uint8) * 255

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return False

    cell_area = cell.shape[0] * cell.shape[1]
    min_area = max(15, cell_area * min_area_ratio)  # scales with cell/photo size
    return any(cv2.contourArea(cnt) > min_area for cnt in contours)


def extract_attendance_grid(image_path_or_array, resize_to=(900, 1200), sensitivity=60, info_columns=3):
    """
    Main entry point. Reads an attendance-sheet photo and returns a structured result:

    {
        "num_sessions": int,                # how many lecture-columns were detected
        "num_students": int,                # how many student-rows were detected
        "rows": [ [bool, bool, ...], ... ]   # rows[i][j] = True means ABSENT
        "debug_image": np.ndarray            # image with green/red boxes drawn (for preview)
    }

    `sensitivity` (0-255, default 60) controls how strong a color has to be to
    count as a mark. Lower it if real marks are being missed (light/faded pen);
    raise it if shadows/paper texture are being wrongly detected as marks.

    `info_columns` (default 3) is how many non-attendance columns sit to the
    LEFT of the first session column -- e.g. Roll No, Enrolment No, Name = 3.
    This is NOT the same for every register: some only have Roll + Name (2),
    some add Division too (4). Get this wrong and every session column reads
    from the wrong place -- attendance will look "random"/shifted even though
    the grid itself was detected correctly. Set it to match whatever register
    photo is being uploaded (exposed as a UI input in the Streamlit app).

    Raises ValueError if the grid could not be confidently detected (e.g. blurry photo,
    no visible table lines) -- the caller (Streamlit UI) should catch this and ask the
    user to retake/re-upload the photo rather than silently producing garbage data.
    """
    if isinstance(image_path_or_array, str):
        img = cv2.imread(image_path_or_array)
        if img is None:
            raise ValueError("Could not read the image file.")
    else:
        img = image_path_or_array

    img = cv2.resize(img, resize_to)

    # Safety padding: if the table's outer border sits exactly on the photo's edge
    # (tightly cropped photo, no margin), the grid-line detector can miss that
    # border entirely and silently misread the whole grid by one row/column.
    # Adding a white margin guarantees every border line has room to be detected.
    pad = 30
    img = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(255, 255, 255))

    rows = _detect_grid_points(img)

    if len(rows) < 3:
        raise ValueError(
            "Grid lines nahi mil paayi is photo mein. Photo seedhi, achi roshni mein, "
            "aur poori table frame mein lo, phir dobara try karo."
        )

    # Every row-line should have the same number of points (= columns + 1).
    # If they don't match, the grid detection is unreliable -- fail loudly instead
    # of silently producing wrong data.
    col_counts = {len(r) for r in rows}
    if len(col_counts) != 1:
        raise ValueError(
            "Grid asymmetric detect hui (kuch lines mein columns match nahi ho rahe). "
            "Photo mein table poori tarah straight aur bina shadow ke honi chahiye."
        )

    num_cols = len(rows[0]) - 1 - info_columns  # minus the info columns (Roll/Enrol/Name/...) and the outer right border
    num_students = len(rows) - 2  # minus header row and bottom border

    if num_cols < 1 or num_students < 1:
        raise ValueError(
            "Table bahut choti detect hui, ya info_columns (Roll/Enrol/Name count) galat set hai -- "
            "check karo photo poora frame mein hai aur info_columns sahi count hai."
        )

    debug_img = img.copy()
    result_rows = []

    for i in range(1, len(rows) - 1):
        student_row = []
        for j in range(info_columns, len(rows[i]) - 1):
            x1, y1 = rows[i][j]
            x2, y2 = rows[i + 1][j + 1]
            cell = img[y1:y2, x1:x2]
            absent = _is_absent(cell, saturation_threshold=sensitivity)
            student_row.append(absent)

            color = (0, 0, 255) if absent else (0, 255, 0)
            cv2.rectangle(debug_img, (x1, y1), (x2, y2), color, 2)

        result_rows.append(student_row)

    return {
        "num_sessions": num_cols,
        "num_students": num_students,
        "rows": result_rows,
        "debug_image": debug_img,
    }


if __name__ == "__main__":
    # Quick manual test: python3 ocr_engine.py
    result = extract_attendance_grid("attendance.png")
    print(f"Detected {result['num_students']} students x {result['num_sessions']} sessions")
    for i, row in enumerate(result["rows"]):
        present = result["num_sessions"] - sum(row)
        print(f"Row {i+1}: present={present}/{result['num_sessions']}  absent_cols={[j+1 for j,v in enumerate(row) if v]}")