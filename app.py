# Al tope del archivo SOLO deja imports ligeros
import os, io, re, tempfile, json
import streamlit as st
import pandas as pd
from unidecode import unidecode

st.set_page_config(page_title="Escáner PDFs (Drive) — Venta DM + FOLIO", page_icon="📄", layout="wide")
st.title("📄 Escáner de PDFs en Google Drive → Venta DM & FOLIO")

# ... helpers de regex y normalización (no pesados) ...

def get_drive_from_secret_or_ui():
    # NO importes pydrive2 aquí arriba; hazlo aquí dentro:
    from pydrive2.auth import GoogleAuth
    from pydrive2.drive import GoogleDrive

    sa = st.secrets.get("GDRIVE_SERVICE_JSON") or st.session_state.get("sa_json_parsed")
    if not sa:
        st.error("Falta GDRIVE_SERVICE_JSON (en Secrets o pegado en la UI).")
        st.stop()

    with open("service_account.json", "w", encoding="utf-8") as f:
        f.write(json.dumps(sa) if isinstance(sa, dict) else sa)

    gauth = GoogleAuth(settings={"client_config_backend": "service",
                                 "service_config": {"client_json_file_path": "service_account.json"}})
    gauth.ServiceAuth()
    return GoogleDrive(gauth)

def extract_text_lazy(path: str) -> str:
    # Importa pdfminer sólo cuando se necesite
    from pdfminer.high_level import extract_text
    try:
        return extract_text(path) or ""
    except Exception:
        return ""

# … tu UI de pegar/verificar secreto …
# … tu UI de pegar carpeta y botón “Escanear PDFs” …

if st.button("🚀 Escanear PDFs", type="primary"):
    drive = get_drive_from_secret_or_ui()       # <- importa pydrive2 aquí
    # resto del flujo (listar descargas, extract_text_lazy, etc.)
