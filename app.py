# app.py — Escáner PDFs en Google Drive (Venta DM, Shopify, Amazon) + FOLIO + TOTAL

import os, io, re, tempfile, json
from typing import List, Tuple, Dict
from collections.abc import Mapping

import pandas as pd
import streamlit as st
from unidecode import unidecode
from pdfminer.high_level import extract_text
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive


st.set_page_config(page_title="Escáner PDFs — Venta DM", page_icon="📄", layout="wide")
st.title("📄 Escáner de PDFs → MercadoLibre / Amazon / Shopify | Folios & Totales")

# ===================== Regexs =====================

# 🆕 Capturar numero tipo MercadoLibre / Amazon / Shopify (con guiones)
ORDER_REGEX = r"(?:venta\s*dm|dm|amazon|mercadolibre|shopify)[^\d]{0,20}([0-9][0-9\-\s]{6,})"

# FOLIO
FOLIO_REGEX = r"(folio[^A-Za-z0-9]{0,5})([A-Z0-9\-\/]{4,})"

# TOTAL captura ej: TOTAL  $ 217.00 | Total MXN 1,234.50
TOTAL_REGEX = r"total[^0-9]{0,10}(\$?\s?[0-9\.,]{2,})"

# Detectar plataforma
def detect_platform(text: str) -> str:
    t = text.lower()
    if "mercado" in t or "meli" in t:
        return "MercadoLibre"
    if "shopify" in t:
        return "Shopify"
    if "amazon" in t:
        return "Amazon"
    return ""

# Normalizar
def normalize_text(txt: str) -> str:
    t = unidecode(txt or "")
    t = t.replace("\n", " ")
    t = re.sub(r"[ \t]+", " ", t)
    return t

# Extraer datos de un PDF
def extract_fields(text: str):
    t = normalize_text(text).lower()

    # Plataforma
    platform = detect_platform(t)

    # Numero de orden (con guiones)
    order = ""
    m = re.search(ORDER_REGEX, t, flags=re.IGNORECASE)
    if m:
        order = m.group(1).strip().replace(" ", "")

    # Folio
    folio = ""
    mf = re.search(FOLIO_REGEX, t, flags=re.IGNORECASE)
    if mf:
        folio = mf.group(2).strip()

    # Total
    total = ""
    mt = re.search(TOTAL_REGEX, t, flags=re.IGNORECASE)
    if mt:
        total = mt.group(1).replace("$", "").replace(",", "").strip()

    return platform, order, folio, total

# ===================== Google Drive Auth =====================

def get_drive():
    if st.secrets.get("GDRIVE_SERVICE_JSON", None):
        sa_json_obj = st.secrets["GDRIVE_SERVICE_JSON"]
    elif "sa_json_parsed" in st.session_state:
        sa_json_obj = st.session_state["sa_json_parsed"]
    else:
        st.error("❌ No Google Drive credentials. Add in Secrets or paste JSON.")
        st.stop()

    # Convertir a dict JSON válido
    if isinstance(sa_json_obj, str):
        try:
            sa = json.loads(sa_json_obj)
        except:
            st.error("❌ Invalid JSON format in secret.")
            st.stop()
    elif isinstance(sa_json_obj, Mapping):
        sa = dict(sa_json_obj)
    else:
        sa = sa_json_obj

    with open("service_account.json", "w", encoding="utf-8") as f:
        f.write(json.dumps(sa))

    gauth = GoogleAuth(settings={
        "client_config_backend": "service",
        "service_config": {"client_json_file_path": "service_account.json"}
    })
    gauth.ServiceAuth()
    return GoogleDrive(gauth)

# ===================== Drive Helpers =====================

def parse_folder_id(inp: str) -> str:
    inp = (inp or "").strip()
    m = re.search(r"/folders/([^/?]+)", inp)
    if m: return m.group(1)
    return inp

def list_pdfs(drive, folder_id):
    q = f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false"
    return drive.ListFile({"q": q}).GetList()

def read_pdf(drive, file_id):
    f = drive.CreateFile({"id": file_id})
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    f.GetContentFile(path)
    return path

# ===================== UI Credenciales =====================

st.subheader("🔐 Google Drive Credentials")

sa_input = st.text_area("Pega JSON (si no está en Secrets)", height=150)
if st.button("Guardar en sesión"):
    try:
        st.session_state["sa_json_parsed"] = json.loads(sa_input)
        st.success("✅ JSON cargado")
    except:
        st.error("❌ JSON inválido")

# ===================== UI Carpeta Drive =====================

st.subheader("📂 Carpeta de Drive")

folder = st.text_input("Pega URL o ID de la carpeta")

if st.button("🚀 Escanear PDFs", type="primary"):
    folder_id = parse_folder_id(folder)
    if not folder_id:
        st.error("❌ URL/ID inválida")
        st.stop()

    with st.spinner("Autenticando…"):
        drive = get_drive()

    with st.spinner("Buscando PDFs…"):
        files = list_pdfs(drive, folder_id)

    if not files:
        st.warning("No hay PDFs en esta carpeta")
        st.stop()

    st.success(f"📄 {len(files)} PDFs encontrados")

    rows = []
    prog = st.progress(0.0)

    for i, f in enumerate(files, start=1):
        try:
            path = read_pdf(drive, f["id"])
            text = extract_text(path)
            os.remove(path)

            platform, order, folio, total = extract_fields(text)
            rows.append({
                "archivo": f["title"],
                "plataforma": platform,
                "numero_venta": order,
                "folio": folio,
                "total": total
            })
        except:
            rows.append({"archivo": f["title"], "plataforma":"", "numero_venta":"", "folio":"", "total":""})

        prog.progress(i/len(files))

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Resultados")

    st.download_button("⬇️ Descargar Excel", data=buf.getvalue(),
                       file_name="resultados_ventas.xlsx",
                       mime="application/vnd.ms-excel")

