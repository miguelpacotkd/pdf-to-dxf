import fitz
import ezdxf
from ezdxf import transform
import math
import re

# ----------------------------
# CONFIG
# ----------------------------
RED_TOL = 0.25
TEXT_DISTANCE_THRESHOLD = 50


# ----------------------------
# HELPERS
# ----------------------------
def is_red(color):
    if not color:
        return False

    try:
        r, g, b = color
        return r > 0.7 and g < RED_TOL and b < RED_TOL
    except:
        return False


def line_length(p1, p2):
    return math.dist(p1, p2)


def midpoint(p1, p2):
    return ((p1[0]+p2[0])/2, (p1[1]+p2[1])/2)


def parse_number(text):
    text = text.replace(",", ".")
    match = re.search(r"\d+(\.\d+)?", text)
    return float(match.group()) if match else None


def distance(p, q):
    return math.dist(p, q)

# ----------------------------
# BEZIER SAMPLING (curve → polyline)
# ----------------------------
def bezier_point(p0, p1, p2, p3, t):
    return (
        (1-t)**3 * p0[0] + 3*(1-t)**2*t*p1[0] + 3*(1-t)*t**2*p2[0] + t**3*p3[0],
        (1-t)**3 * p0[1] + 3*(1-t)**2*t*p1[1] + 3*(1-t)*t**2*p2[1] + t**3*p3[1]
    )

def sample_bezier(p0, p1, p2, p3, steps=20):
    return [bezier_point(p0, p1, p2, p3, t/steps) for t in range(steps+1)]


# ----------------------------
# MAIN EXTRACTION
# ----------------------------
def extract_entities(page):
    entities = []

    for drawing in page.get_drawings():
        color = drawing.get("color", (0, 0, 0)) or (0, 0, 0)

        for item in drawing["items"]:

            if item[0] == "l":
                p1, p2 = item[1], item[2]
                entities.append(("LINE", p1, p2, color))

            elif item[0] == "re":
                rect = item[1]
                x0, y0, x1, y1 = rect

                pts = [
                    (x0, y0),
                    (x1, y0),
                    (x1, y1),
                    (x0, y1),
                    (x0, y0)
                ]
                entities.append(("LWPOLYLINE", pts, color))

            elif item[0] == "c":
                p0, p1, p2, p3 = item[1:]
                pts = sample_bezier(p0, p1, p2, p3)
                entities.append(("LWPOLYLINE", pts, color))

    return entities

def to_dxf_color(rgb):
    r, g, b = rgb
    return (int(r*255), int(g*255), int(b*255))


# ----------------------------
# CREATE DXF (HIGH QUALITY)
# ----------------------------
def create_dxf(entities):
    doc = ezdxf.new()
    msp = doc.modelspace()

    for ent in entities:

        if ent[0] == "LINE":
            _, p1, p2, color = ent
            rgb = to_dxf_color(color)

            e = msp.add_line(p1, p2)
            e.dxf.true_color = (rgb[0] << 16) + (rgb[1] << 8) + rgb[2]

        elif ent[0] == "LWPOLYLINE":
            _, pts, color = ent
            rgb = to_dxf_color(color)

            e = msp.add_lwpolyline(pts)
            e.dxf.true_color = (rgb[0] << 16) + (rgb[1] << 8) + rgb[2]

    return doc


# ----------------------------
# STEP 3: FIND SCALE
# ----------------------------
def get_red_lines(page):
    red_lines = []

    for drawing in page.get_drawings():
        color = drawing.get("color", (0, 0, 0))

        if not is_red(color):
            continue

        for item in drawing["items"]:
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                length = line_length(p1, p2)
                red_lines.append((length, p1, p2))

    red_lines.sort(reverse=True, key=lambda x: x[0])
    return red_lines


def find_dimension(page, line):
    length, p1, p2 = line
    mid = midpoint(p1, p2)

    best_val = None
    best_dist = float("inf")

    for block in page.get_text("blocks"):
        x0, y0, x1, y1, text, *_ = block

        val = parse_number(text)
        if val is None:
            continue

        if val < 10 or val > 10000:
            continue

        text_center = ((x0 + x1)/2, (y0 + y1)/2)
        d = distance(mid, text_center)

        if d < best_dist and d < TEXT_DISTANCE_THRESHOLD:
            best_dist = d
            best_val = val

    return best_val


def compute_scale(page):
    red_lines = get_red_lines(page)

    for line in red_lines[:3]:
        measured = line[0]
        real = find_dimension(page, line)

        if real:
            print(f"Measured: {measured}")
            print(f"Detected real: {real}")
            return real / measured

    raise Exception("Scale detection failed")


# ----------------------------
# STEP 4: APPLY SCALE
# ----------------------------

def scale_doc(doc, scale):
    msp = doc.modelspace()
    transform.scale_uniform(msp, scale)

# ----------------------------
# MAIN PIPELINE
# ----------------------------
def pdf_to_scaled_dxf(pdf_path, output_dxf):
    doc_pdf = fitz.open(pdf_path)
    page = doc_pdf[0]

    # Extract geometry
    entities = extract_entities(page)

    # Create DXF
    doc_dxf = create_dxf(entities)

    # Compute scale
    try:
        scale = compute_scale(page)
        print(f"Scale factor: {scale}")
    except:
        print("⚠️ Scale failed → using 1.0")
        scale = 1.0

    # Apply scale
    scale_doc(doc_dxf, scale)

    # Save
    doc_dxf.saveas(output_dxf)
# ----------------------------
# RUN
# ----------------------------
import os

PDF_DIR = r"path for pdfs"
OUT_DIR = r"path for dwgs"


def process_batch():
    os.makedirs(OUT_DIR, exist_ok=True)

    success = 0
    failed = []

    files = [f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf")]

    print(f"Found {len(files)} PDFs")

    for file in files:
        name = os.path.splitext(file)[0]

        pdf_path = os.path.join(PDF_DIR, file)
        out_path = os.path.join(OUT_DIR, name + ".dxf")

        print(f"\nProcessing: {file}")

        try:
            pdf_to_scaled_dxf(pdf_path, out_path)
            success += 1

        except Exception as e:
            print(f"❌ Failed: {e}")
            failed.append((file, str(e)))
            
    if failed:
        print("\nFailures:")
        for f in failed:
            print(f)

process_batch()

