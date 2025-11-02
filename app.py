# app.py — Escáner PDFs Google Drive: Venta DM (Mercado Libre / Shopify) + Folio

import os, io, re, tempfile, json
from typing import List, Dict, Tuple

import pandas as pd
import streamlit as st
from unidecode import unidecode

from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

st.set_page_config(page_title="Escáner PDFs Drive — Venta DM & FOLIO", page_icon="📄", layout="wide")
st.title("📄 Escáner de PDFs en Google Drive → Venta DM + Folio (ML / Shopify)")
st.caption("Pega la carpeta de Drive, escanea PDFs y descarga Excel con Venta DM + Folio.")

# --------------------- Regex ---------------------
# MercadoLibre y Shopify
VENTA_DM_REGEXES = [
    r"venta\s*dm[^0-9]{0,30}mercado\s*libre[^0-9]{0,20}[-: ]\s*([0-9]{3,})",
    r"venta\s*dm[^0-9]{0,30}shopify[^0-9]{0,20}[-: ]\s*([0-9]{3,})",
    r"venta\s*dm[^0-9]{0,30}[-: ]\s*([0-9]{3,})",  # fallback general
]

FOLIO_REGEXES = [
    r"folio\s*[:\-]\s*([A-Z0-9\-\/]{3,})",
    r"\bfolio\b\s+([A-Z0-9\-\/]{3,})",
]

# --------------------- Helpers ---------------------
def normalize_text(txt: str) -> str:
    t = unidecode(txt or "")
    t = re.sub(r"[ \t]+", " ", t)
    return t

def extract_fields_from_text(text: str) -> Tuple[str, str]:
    t = normalize_text(text).lower()
    venta_dm, folio = "", ""

    # Buscar Venta DM
    for pat in VENTA_DM_REGEXES:
        m = re.search(pat, t, flags=re.IGNORECASE | re.DOTALL)
        if m:
            venta_dm = m.group(1).strip()
            break

    # Buscar Folio
    for pat in FOLIO_REGEXES:
        m = re.search(pat, t, flags=re.IGNORECASE | re.DOTALL)
        if m:
            folio = m.group(1).strip().strip(".;,")
            break

    return venta_dm, folio

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
        from pdfminer.high_level import extract_text   # ✅ import correcto
        return extract_text(path) or ""
    except:
        return ""

# --------------------- Google Drive Auth ---------------------
def get_drive() -> GoogleDrive:
    sa_json_obj = None

    # Prefer Secrets, fallback a sesión
    if st.secrets.get("GDRIVE_SERVICE_JSON", None):
        sa_json_obj = st.secrets["GDRIVE_SERVICE_JSON"]
    elif "sa_json_parsed" in st.session_state:
        sa_json_obj = st.session_state["sa_json_parsed"]

    if not sa_json_obj:
        st.error("❌ No credentials. Paste JSON and save above.")
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
    "Pega el JSON de la cuenta de servicio",
    value=st.session_state.get("sa_json_text", ""),
    height=160
)

if st.button("💾 Guardar JSON en sesión"):
    try:
        parsed = json.loads(sa_json_text)
        assert "client_email" in parsed
        st.session_state["sa_json_text"] = sa_json_text
        st.session_state["sa_json_parsed"] = parsed
        st.success(f"✅ JSON OK — {parsed.get('client_email')}")
    except Exception as e:
        st.error(f"JSON inválido: {e}")

# --------------------- Pestañas ---------------------
tab1, tab2 = st.tabs(["📂 Leer Carpeta Drive", "📤 Subir PDFs Manual"])

# ================= TAB 1 ==================
with tab1:
    folder_input = st.text_input(
        "URL/ID carpeta Drive:",
        placeholder="https://drive.google.com/drive/folders/xxxxxxxx"
    )

    if st.button("🚀 Escanear PDFs", type="primary"):
        folder_id = parse_folder_id_from_input(folder_input)
        if not folder_id:
            st.error("❌ No pude detectar folder ID")
            st.stop()

        st.info("Autenticando con Drive…")
        drive = get_drive()

        st.info("Buscando PDFs…")
        files = list_pdfs_in_folder(drive, folder_id)

        if not files:
            st.warning("⚠️ No hay PDFs en la carpeta.")
            st.stop()

        st.success(f"Encontrados {len(files)} PDFs ✅")
        prog = st.progress(0)
        rows = []

        for i, meta in enumerate(files, start=1):
            tmp = download_pdf_temp(drive, meta["id"])
            text = extract_text_from_pdf(tmp)
            os.remove(tmp)

            venta_dm, folio = extract_fields_from_text(text)

            if venta_dm:  # Solo guardar PDFs donde hubo match
                rows.append({
                    "archivo": meta["title"],
                    "venta_dm": venta_dm,
                    "folio": folio
                })

            prog.progress(i/len(files))

        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, height=400)

        # Exportar Excel
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine="openpyxl") as w:
            df.to_excel(w, index=False)

        st.download_button(
            "⬇️ Descargar Excel",
            data=out.getvalue(),
            file_name="venta_dm_folios.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

# ================= TAB 2 ==================
with tab2:
    files = st.file_uploader("Sube PDF(s)", type="pdf", accept_multiple_files=True)

    if files:
        rows = []
        for f in files:
            fd, tmp = tempfile.mkstemp(suffix=".pdf"); os.close(fd)
            with open(tmp, "wb") as h: h.write(f.read())
            text = extract_text_from_pdf(tmp); os.remove(tmp)

            venta_dm, folio = extract_fields_from_text(text)
            if venta_dm:
                rows.append({"archivo": f.name, "venta_dm": venta_dm, "folio": folio})

        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True)

        out = io.BytesIO()
        with pd.ExcelWriter(out, engine="openpyxl") as w:
            df.to_excel(w, index=False)

        st.download_button(
            "⬇️ Descargar Excel Manual",
            data=out.getvalue(),
            file_name="manual_venta_dm.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
