# app.py — Escáner PDFs en Google Drive: Venta DM (ML / Amazon / Shopify) + FOLIO + TOTAL

import os, io, re, tempfile, json
from typing import List, Tuple, Dict, Optional
from collections.abc import Mapping

import pandas as pd
import streamlit as st
from unidecode import unidecode
from pdfminer.high_level import extract_text
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

st.set_page_config(page_title="Escáner PDFs — Venta DM", page_icon="📄", layout="wide")
st.title("📄 Escáner de PDFs → MercadoLibre / Amazon / Shopify | Venta DM, Folio y Total")


# ===================== Normalización =====================

def normalize_text(txt: str) -> str:
    """Quita acentos, normaliza espacios y baja a minúsculas (sin perder números)."""
    t = unidecode(txt or "")
    t = t.replace("\r", " ").replace("\n", " ")
    t = re.sub(r"[ \t]+", " ", t)
    return t.lower()


# ===================== Detección de plataforma =====================

def detect_platform(t: str) -> str:
    if "mercado" in t or "meli" in t:
        return "MercadoLibre"
    if "shopify" in t:
        return "Shopify"
    if "amazon" in t:
        return "Amazon"
    return ""


# ===================== Extracción por plataforma =====================

# Mercado Libre: normalmente un número largo (10–20 dígitos), casi siempre con "Venta DM Mercado Libre"
PAT_ML_CTX = re.compile(r"venta\s*dm.{0,40}mercado\s*libre.{0,40}[-:\s]\s*([0-9]{10,20})", re.I | re.S)
# Fallback ML: cualquier 13–20 dígitos que empiece por 2000... (típico en tus ejemplos)
PAT_ML_FALLBACK = re.compile(r"\b(2000[0-9]{9,15})\b")

# Amazon: patrón con guiones tipo 702-5831275-1421011
PAT_AMZ_CTX = re.compile(r"venta\s*dm.{0,40}amazon.{0,40}[-:\s]\s*([0-9]{2,}-[0-9]{5,}-[0-9]{5,})", re.I | re.S)
PAT_AMZ_ANY = re.compile(r"\b([0-9]{2,}-[0-9]{5,}-[0-9]{5,})\b")

# Shopify: típicamente 4 dígitos (4368, 4361, etc.) con contexto Shopify
PAT_SHOP_CTX = re.compile(r"venta\s*dm.{0,40}shopify.{0,40}[-:\s]\s*([0-9]{3,6})", re.I | re.S)
PAT_SHOP_ANY = re.compile(r"\b([3-9][0-9]{3,5})\b")  # evita 010101000 capturas espurias

# Folio: SOLO números (evita capturar “fiscal”)
PAT_FOLIO_NUM = re.compile(r"\bfolio\b[^0-9]{0,6}([0-9]{5,7})", re.I)
# Fallback de folio desde el nombre de archivo (ej. F3_131640.pdf)
PAT_FOLIO_FILENAME = re.compile(r"[Ff]3[_-]?([0-9]{5,})")

# Total: tolerante (TOTAL $ 217.00, Total MXN 1,234.56, etc.)
PAT_TOTAL = re.compile(r"total[^0-9]{0,12}(?:mxn|usd|us\$|\$)?\s*([0-9]{1,3}(?:[.,][0-9]{3})*(?:[.,][0-9]{2})|[0-9]+(?:[.,][0-9]{2}))", re.I | re.S)


def parse_amount(raw: str) -> Optional[float]:
    if not raw:
        return None
    s = raw.strip()
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except:
        return None


def find_total(text_raw: str) -> Optional[float]:
    matches = list(PAT_TOTAL.finditer(text_raw))
    if not matches:
        return None
    last = matches[-1].group(1)
    return parse_amount(last)


def extract_fields_from_pdf_text(text: str, filename: str) -> Tuple[str, str, str, Optional[float]]:
    """
    Devuelve (plataforma, venta_dm, folio, total).
    - venta_dm se obtiene por contexto de plataforma para evitar números basura.
    - folio sólo numérico; fallback al nombre del archivo (F3_######).
    - total se parsea a float cuando es posible.
    """
    t = normalize_text(text)
    platform = detect_platform(t)

    venta_dm = ""

    if platform == "MercadoLibre":
        m = PAT_ML_CTX.search(t)
        if m:
            venta_dm = m.group(1).strip()
        else:
            m2 = PAT_ML_FALLBACK.search(t)
            venta_dm = m2.group(1) if m2 else ""
    elif platform == "Amazon":
        m = PAT_AMZ_CTX.search(t)
        if m:
            venta_dm = m.group(1).strip()
        else:
            m2 = PAT_AMZ_ANY.search(t)
            venta_dm = m2.group(1) if m2 else ""
    elif platform == "Shopify":
        m = PAT_SHOP_CTX.search(t)
        if m:
            venta_dm = m.group(1).strip()
        else:
            # para evitar capturas erróneas, usa ANY solo si ya detectamos palabra Shopify en el texto
            m2 = PAT_SHOP_ANY.search(t)
            venta_dm = m2.group(1) if m2 else ""
    else:
        # Si no detectamos plataforma, probamos en orden con patrones característicos para minimizar falsos positivos
        m = PAT_ML_FALLBACK.search(t)
        if m:
            platform = "MercadoLibre"
            venta_dm = m.group(1)
        else:
            m = PAT_AMZ_ANY.search(t)
            if m:
                platform = "Amazon"
                venta_dm = m.group(1)
            else:
                # último recurso: NO tomar genéricos para no introducir "010101000"
                venta_dm = ""

    # FOLIO: sólo dígitos
    folio = ""
    mf = PAT_FOLIO_NUM.search(text)  # usa texto original por si hay OCR raro
    if mf:
        folio = mf.group(1).strip()
    else:
        # fallback por nombre de archivo p.ej. FE_DMM920422196_F3_131640.pdf
        fn_match = PAT_FOLIO_FILENAME.search(filename or "")
        if fn_match:
            folio = fn_match.group(1)

    total = find_total(text)

    return platform, venta_dm, folio, total


# ===================== Google Drive Auth (robusto para Secrets) =====================

def get_drive() -> GoogleDrive:
    sa_json_obj = None
    if st.secrets.get("GDRIVE_SERVICE_JSON", None):
        sa_json_obj = st.secrets["GDRIVE_SERVICE_JSON"]
    elif "sa_json_parsed" in st.session_state:
        sa_json_obj = st.session_state["sa_json_parsed"]

    if not sa_json_obj:
        st.error("❌ No credentials. Paste JSON below or add to Secrets as GDRIVE_SERVICE_JSON.")
        st.stop()

    # Normalizar a dict
    if isinstance(sa_json_obj, str):
        try:
            sa = json.loads(sa_json_obj.strip())
        except Exception:
            st.error("❌ GDRIVE_SERVICE_JSON is not valid JSON (string). Use TOML table or triple-quoted JSON with \\n in private_key.")
            st.stop()
    elif isinstance(sa_json_obj, Mapping):
        sa = dict(sa_json_obj)
    elif isinstance(sa_json_obj, dict):
        sa = sa_json_obj
    else:
        st.error(f"❌ Unexpected type for GDRIVE_SERVICE_JSON: {type(sa_json_obj)}")
        st.stop()

    with open("service_account.json", "w", encoding="utf-8") as f:
        f.write(json.dumps(sa))

    gauth = GoogleAuth(settings={
        "client_config_backend": "service",
        "service_config": {"client_json_file_path": "service_account.json"}
    })
    gauth.ServiceAuth()
    return GoogleDrive(gauth)


# ===================== Drive helpers =====================

def parse_folder_id(folder_input: str) -> str:
    s = (folder_input or "").strip()
    m = re.search(r"/folders/([a-zA-Z0-9_\-]{10,})", s)
    if m: return m.group(1)
    m = re.search(r"[?&]id=([a-zA-Z0-9_\-]{10,})", s)
    if m: return m.group(1)
    return s

def list_pdfs(drive: GoogleDrive, folder_id: str) -> List[Dict]:
    q = f"'{folder_id}' in parents and mimeType = 'application/pdf' and trashed = false"
    return [{"id": f["id"], "title": f["title"]} for f in drive.ListFile({"q": q}).GetList()]

def download_pdf_temp(drive: GoogleDrive, file_id: str) -> str:
    f = drive.CreateFile({"id": file_id})
    fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    f.GetContentFile(tmp_path)
    return tmp_path


# ===================== UI Credenciales =====================

st.subheader("🔐 Google Drive Service Account JSON (opcional si ya está en Secrets)")
sa_json_text = st.text_area("Pega el JSON aquí si no lo guardaste en Secrets", height=160)
if st.button("💾 Guardar en sesión"):
    try:
        st.session_state["sa_json_parsed"] = json.loads(sa_json_text)
        st.success("✅ JSON válido guardado en sesión")
    except Exception as e:
        st.error(f"❌ JSON inválido: {e}")


# ===================== UI de procesamiento =====================

st.subheader("📂 Carpeta de Drive")
folder_input = st.text_input("URL o ID de la carpeta en Drive")
go = st.button("🚀 Escanear PDFs", type="primary")

if go:
    folder_id = parse_folder_id(folder_input)
    if not folder_id:
        st.error("❌ No pude detectar el ID de carpeta. Revisa la URL/ID.")
        st.stop()

    with st.spinner("Autenticando con Google Drive…"):
        drive = get_drive()

    with st.spinner("Listando PDFs…"):
        files = list_pdfs(drive, folder_id)

    if not files:
        st.warning("No se encontraron PDFs en esa carpeta.")
        st.stop()

    st.success(f"Encontrados {len(files)} PDFs. Procesando…")
    rows = []
    prog = st.progress(0.0)

    for i, meta in enumerate(files, start=1):
        try:
            tmp_pdf = download_pdf_temp(drive, meta["id"])
            text = extract_text(tmp_pdf) or ""
            os.remove(tmp_pdf)

            platform, venta_dm, folio, total = extract_fields_from_pdf_text(text, meta["title"])

            # Sólo guardamos filas con venta_dm real (evita 010101000)
            if venta_dm:
                rows.append({
                    "archivo": meta["title"],
                    "plataforma": platform,
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

    if not rows:
        st.warning("Se procesaron los PDFs, pero no se encontró ninguna 'Venta DM' válida.")
        st.stop()

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, height=420)

    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Resultados")

    st.download_button(
        "⬇️ Descargar Excel",
        data=out.getvalue(),
        file_name="venta_dm_folios_totales.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )
