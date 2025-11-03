# app.py — Escáner PDFs Google Drive: Venta DM (ML / Shopify / Amazon) + Folio + Total

import os, io, re, tempfile, json
from typing import List, Dict, Tuple, Optional

import pandas as pd
import streamlit as st
from unidecode import unidecode

from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

st.set_page_config(page_title="Escáner PDFs — Venta DM, Folio y Total", page_icon="📄", layout="wide")
st.title("📄 Escáner de PDFs en Google Drive → Venta DM + Folio + Total")
st.caption("Pega la carpeta de Drive, escanea PDFs y descarga Excel con Venta DM, Folio, Total y Plataforma.")

# --------------------- Regex: Venta DM por plataforma ---------------------
# Amazon: permite dígitos y guiones (p.ej., 702-5831275-1421011)
PAT_AMAZON = r"(?:venta\s*)?dm[^a-z0-9]{0,30}amazon[^0-9]{0,30}[-:]\s*([0-9][0-9\-]{6,})"
# Shopify: números cortos (≥3)
PAT_SHOPIFY = r"(?:venta\s*)?dm[^a-z0-9]{0,30}shopify[^0-9]{0,30}[-:]\s*([0-9]{3,})"
PAT_SHOPIFY_ALT = r"shopify[^0-9]{0,30}[-:]\s*([0-9]{3,})"
# Mercado Libre: números largos (≥6)
PAT_ML = r"(?:venta\s*)?dm[^0-9]{0,30}mercado\s*libre[^0-9]{0,30}[-: ]\s*([0-9]{6,})"
# Fallback genérico (acepta dígitos y guiones por si el PDF no trae el canal)
PAT_FALLBACK = r"(?:venta\s*)?dm[^0-9]{0,30}[-: ]\s*([0-9][0-9\-]{2,})"

VENTA_DM_PATTERNS = [
    ("Amazon", PAT_AMAZON),
    ("Shopify", PAT_SHOPIFY),
    ("Shopify", PAT_SHOPIFY_ALT),
    ("MercadoLibre", PAT_ML),
    ("Generico", PAT_FALLBACK),
]

# Folio
FOLIO_REGEXES = [
    r"folio\s*[:\-]\s*([A-Z0-9\-\/]{3,})",
    r"\bfolio\b\s+([A-Z0-9\-\/]{3,})",
]

# Total (tolerante a $, MXN, separadores y espacios)
# Captura números tipo 217.00, 1,234.56, 1.234,56, etc. después de la palabra TOTAL
TOTAL_REGEX = r"total[^0-9]{0,12}(?:mxn|usd|us\$|\$)?\s*([0-9]{1,3}(?:[.,][0-9]{3})*(?:[.,][0-9]{2})|[0-9]+(?:[.,][0-9]{2}))"

# --------------------- Helpers ---------------------
def normalize_text(txt: str) -> str:
    t = unidecode(txt or "")
    t = re.sub(r"[ \t]+", " ", t)
    return t

def parse_amount(raw: str) -> Optional[float]:
    """Convierte '1,234.56' o '1.234,56' o '217.00' en float. Devuelve None si falla."""
    if not raw:
        return None
    s = raw.strip()
    # Detectar formato
    if "," in s and "." in s:
        # decidir decimal por última ocurrencia de , o .
        if s.rfind(",") > s.rfind("."):
            # decimal = ',', miles = '.'
            s = s.replace(".", "").replace(",", ".")
        else:
            # decimal = '.', miles = ','
            s = s.replace(",", "")
    else:
        # Solo un separador o ninguno
        if "," in s:
            # asume coma decimal
            s = s.replace(".", "").replace(",", ".")
        # si solo punto, se deja
    try:
        return float(s)
    except:
        return None

def find_total(text_raw: str) -> Optional[float]:
    """Busca TOTAL ... <monto>. Devuelve float o None. Toma la última coincidencia."""
    # Trabajamos sobre texto normalizado pero sin bajar a minúsculas para no romper números
    t = normalize_text(text_raw)
    matches = list(re.finditer(TOTAL_REGEX, t, flags=re.IGNORECASE | re.DOTALL))
    if not matches:
        return None
    last = matches[-1].group(1)
    return parse_amount(last)

def extract_fields_from_text(text: str) -> Tuple[str, str, str, Optional[float]]:
    """
    Devuelve (venta_dm, folio, plataforma, total).
    Plataforma ∈ {'Amazon','Shopify','MercadoLibre','Generico',''}.
    """
    t = normalize_text(text).lower()
    venta_dm, folio, plataforma = "", "", ""

    # Venta DM y plataforma (en orden de prioridad)
    for platform_name, pat in VENTA_DM_PATTERNS:
        m = re.search(pat, t, flags=re.IGNORECASE | re.DOTALL)
        if m:
            venta_dm = m.group(1).strip()
            plataforma = platform_name
            break

    # Folio
    for pat in FOLIO_REGEXES:
        m = re.search(pat, t, flags=re.IGNORECASE | re.DOTALL)
        if m:
            folio = m.group(1).strip().strip(".;,")
            break

    # Total (usar texto original, no lowercased)
    total = find_total(text)

    return venta_dm, folio, plataforma, total

def parse_folder_id_from_input(folder_input: str) -> str:
    s = (folder_input or "").strip()
    if not s:
        return ""
    m = re.search(r"/folders/([a-zA-Z0-9_\-]{10,})", s)
    if m: return m.group(1)
    m = re.search(r"[?&]id=([a-zA-Z0-9_\-]{10,})", s)
    if m: return m.group(1)
    return s

# --------------------- PDF Extractor ---------------------
def extract_text_from_pdf(path: str) -> str:
    try:
        from pdfminer.high_level import extract_text  # import diferido
        return extract_text(path) or ""
    except:
        return ""

# --------------------- Google Drive Auth ---------------------
def get_drive() -> GoogleDrive:
    sa_json_obj = None
    if st.secrets.get("GDRIVE_SERVICE_JSON", None):
        sa_json_obj = st.secrets["GDRIVE_SERVICE_JSON"]
    elif "sa_json_parsed" in st.session_state:
        sa_json_obj = st.session_state["sa_json_parsed"]

    if not sa_json_obj:
        st.error("❌ No credentials. Paste JSON below or add to Secrets as GDRIVE_SERVICE_JSON.")
        st.stop()

    with open("service_account.json", "w", encoding="utf-8") as f:
        f.write(json.dumps(sa_json_obj) if isinstance(sa_json_obj, dict) else sa_json_obj)

    gauth = GoogleAuth(settings={
        "client_config_backend": "service",
        "service_config": {"client_json_file_path": "service_account.json"}
    })
    gauth.ServiceAuth()
    return GoogleDrive(gauth)

def list_pdfs_in_folder(drive: GoogleDrive, folder_id: str) -> List[Dict]:
    q = f"'{folder_id}' in parents and mimeType = 'application/pdf' and trashed = false"
    return [{"id": f["id"], "title": f["title"]} for f in drive.ListFile({"q": q}).GetList()]

def download_pdf_temp(drive: GoogleDrive, fid: str) -> str:
    f = drive.CreateFile({"id": fid})
    fd, tmp = tempfile.mkstemp(suffix=".pdf"); os.close(fd)
    f.GetContentFile(tmp)
    return tmp

# --------------------- UI Secreto ---------------------
st.subheader("🔐 Google Drive Service Account JSON")
sa_json_text = st.text_area(
    "Paste the full JSON (or store it in Streamlit Secrets as GDRIVE_SERVICE_JSON)",
    value=st.session_state.get("sa_json_text", ""),
    height=160
)
if st.button("💾 Save JSON to session"):
    try:
        parsed = json.loads(sa_json_text)
        assert "client_email" in parsed
        st.session_state["sa_json_text"] = sa_json_text
        st.session_state["sa_json_parsed"] = parsed
        st.success(f"✅ JSON OK — {parsed.get('client_email')}")
    except Exception as e:
        st.error(f"Invalid JSON: {e}")

# --------------------- Pestañas ---------------------
tab1, tab2 = st.tabs(["📂 Read Drive Folder", "📤 Upload PDFs"])

# ================= TAB 1 ==================
with tab1:
    folder_input = st.text_input(
        "Drive folder URL or ID:",
        placeholder="https://drive.google.com/drive/folders/xxxxxxxx"
    )

    if st.button("🚀 Scan PDFs", type="primary"):
        folder_id = parse_folder_id_from_input(folder_input)
        if not folder_id:
            st.error("❌ Could not detect folder ID")
            st.stop()

        with st.spinner("Authenticating with Drive…"):
            drive = get_drive()

        with st.spinner("Listing PDFs…"):
            files = list_pdfs_in_folder(drive, folder_id)

        if not files:
            st.warning("⚠️ No PDFs in that folder (or the service account doesn't have access).")
            st.stop()

        st.success(f"Found {len(files)} PDFs ✅")
        prog = st.progress(0)
        rows = []

        for i, meta in enumerate(files, start=1):
            try:
                tmp = download_pdf_temp(drive, meta["id"])
                text = extract_text_from_pdf(tmp)
                os.remove(tmp)

                venta_dm, folio, plataforma, total = extract_fields_from_text(text)

                if venta_dm:
                    rows.append({
                        "archivo": meta["title"],
                        "plataforma": plataforma,
                        "venta_dm": venta_dm,
                        "folio": folio,
                        "total": total
                    })
            except Exception:
                rows.append({
                    "archivo": meta.get("title", "(desconocido)"),
                    "plataforma": "",
                    "venta_dm": "",
                    "folio": "",
                    "total": None
                })

            prog.progress(i/len(files))

        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, height=420)

        out = io.BytesIO()
        with pd.ExcelWriter(out, engine="openpyxl") as w:
            df.to_excel(w, index=False, sheet_name="Resultados")
        st.download_button(
            "⬇️ Download Excel",
            data=out.getvalue(),
            file_name="venta_dm_folios_totales.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

# ================= TAB 2 ==================
with tab2:
    files = st.file_uploader("Upload PDF(s)", type="pdf", accept_multiple_files=True)
    if files:
        rows = []
        for f in files:
            try:
                fd, tmp = tempfile.mkstemp(suffix=".pdf"); os.close(fd)
                with open(tmp, "wb") as h: h.write(f.read())
                text = extract_text_from_pdf(tmp); os.remove(tmp)

                venta_dm, folio, plataforma, total = extract_fields_from_text(text)
                if venta_dm:
                    rows.append({
                        "archivo": f.name,
                        "plataforma": plataforma,
                        "venta_dm": venta_dm,
                        "folio": folio,
                        "total": total
                    })
            except Exception:
                rows.append({"archivo": f.name, "plataforma": "", "venta_dm": "", "folio": "", "total": None})

        if rows:
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, height=360)
            out = io.BytesIO()
            with pd.ExcelWriter(out, engine="openpyxl") as w:
                df.to_excel(w, index=False, sheet_name="Resultados")
            st.download_button(
                "⬇️ Download Excel (uploads)",
                data=out.getvalue(),
                file_name="manual_venta_dm_totales.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
