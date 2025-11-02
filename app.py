# app.py — Escáner PDFs en Google Drive (con UI para pegar y verificar el secreto)
# Extrae: número después de "Venta DM ..." y el FOLIO en PDFs de una carpeta de Drive.

import os, io, re, tempfile, json
from typing import List, Tuple, Dict

import pandas as pd
import streamlit as st
from unidecode import unidecode
from pdfminer_high_level import extract_text  # si usas import diferido, puedes revertir a from pdfminer.high_level ...

from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

st.set_page_config(page_title="Escáner PDFs (Drive) — Venta DM + FOLIO", page_icon="📄", layout="wide")
st.title("📄 Escáner de PDFs en Google Drive → Venta DM & FOLIO")
st.caption("Pega la URL/ID de la carpeta de Drive, escanea los PDFs y descarga un Excel.")

# --------------------- Helpers de parsing ---------------------
# 1) Patrones específicos para Shopify (capturan números cortos después de 'Shopify')
# 2) Patrones genéricos para Mercado Libre / otros (números largos)
VENTA_DM_REGEXES = [
    # Ej.: "Venta DM Shopify - 4368" / "Venta DM ... Shopify: 1234"
    r"venta\s*dm[^a-z0-9]{0,30}shopify[^0-9]{0,30}[-:]\s*([0-9]{3,})",
    r"shopify[^0-9]{0,30}[-:]\s*([0-9]{3,})",

    # Genéricos (ML u otros, números largos)
    r"venta\s*dm[^\d]{0,30}[-:]\s*([0-9]{6,})",
    r"venta\s*dm[^\d]{0,30}\s+([0-9]{6,})",
]

FOLIO_REGEXES = [
    r"folio\s*[:\-]\s*([A-Z0-9\-\/]{3,})",
    r"\bfolio\b\s+([A-Z0-9\-\/]{3,})",
]

def normalize_text(txt: str) -> str:
    t = unidecode(txt or "")
    t = re.sub(r"[ \t]+", " ", t)
    return t

def extract_fields_from_text(text: str) -> Tuple[str, str]:
    """Devuelve (venta_dm, folio). Soporta 'Venta DM Shopify - 4368' y casos ML."""
    t = normalize_text(text).lower()
    venta_dm, folio = "", ""

    for pat in VENTA_DM_REGEXES:
        m = re.search(pat, t, flags=re.IGNORECASE | re.DOTALL)
        if m:
            venta_dm = m.group(1).strip()
            break

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

# --------------------- UI: Configurar secreto de servicio ---------------------
st.subheader("🔐 Configurar acceso a Google Drive (Cuenta de servicio)")
with st.expander("Abrir configuración", expanded=True):
    colA, colB = st.columns([3,2])

    with colA:
        st.write("Pega **íntegro** el JSON de la cuenta de servicio (opcional si ya está en *Secrets*).")
        sa_json_text = st.text_area(
            "GDRIVE_SERVICE_JSON",
            value=st.session_state.get("sa_json_text", ""),
            placeholder='{\n  "type": "service_account",\n  "project_id": "...",\n  "private_key_id": "...",\n  "private_key": "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n",\n  "client_email": "xxxx@xxxx.iam.gserviceaccount.com",\n  ...\n}',
            height=180
        )
        save_btn = st.button("💾 Guardar en sesión (no persiste en disco)", use_container_width=True)

        if save_btn:
            try:
                parsed = json.loads(sa_json_text)
                assert "client_email" in parsed, "Falta 'client_email' en el JSON"
                st.session_state["sa_json_text"] = sa_json_text
                st.session_state["sa_json_parsed"] = parsed
                st.success(f"Se guardó en sesión. Cuenta de servicio: {parsed.get('client_email')}")
            except Exception as e:
                st.error(f"JSON inválido: {e}")

    with colB:
        from_secrets = st.secrets.get("GDRIVE_SERVICE_JSON", None) is not None
        from_session = "sa_json_parsed" in st.session_state

        if from_secrets:
            st.success("✅ Encontrado en *Streamlit Secrets* (recomendado en producción).")
        else:
            st.info("ℹ️ No está en *Streamlit Secrets*.")

        if from_session:
            st.success("✅ Encontrado en **sesión** (pegado manualmente).")
        else:
            st.warning("⚠️ Aún no has pegado/guardado el JSON en la sesión.")

        test_btn = st.button("🧪 Probar autenticación", use_container_width=True)

        if test_btn:
            try:
                if from_secrets:
                    sa_json_obj = st.secrets["GDRIVE_SERVICE_JSON"]
                elif from_session:
                    sa_json_obj = st.session_state["sa_json_parsed"]
                else:
                    raise RuntimeError(
                        "No hay credenciales. Usa *Secrets* o pega el JSON y presiona “Guardar en sesión”."
                    )

                sa_path = "service_account.json"
                with open(sa_path, "w", encoding="utf-8") as f:
                    f.write(json.dumps(sa_json_obj) if isinstance(sa_json_obj, dict) else sa_json_obj)

                gauth = GoogleAuth(settings={
                    "client_config_backend": "service",
                    "service_config": {"client_json_file_path": sa_path}
                })
                gauth.ServiceAuth()
                GoogleDrive(gauth)
                st.success("Autenticación correcta ✅. Ya puedes escanear la carpeta.")
                st.caption(f"Cuenta: { (sa_json_obj.get('client_email') if isinstance(sa_json_obj, dict) else '') }")
            except Exception as e:
                st.exception(e)

# --------------------- Autenticación centralizada ---------------------
def get_drive() -> GoogleDrive:
    sa_json_obj = None
    if st.secrets.get("GDRIVE_SERVICE_JSON", None):
        sa_json_obj = st.secrets["GDRIVE_SERVICE_JSON"]
    elif "sa_json_parsed" in st.session_state:
        sa_json_obj = st.session_state["sa_json_parsed"]

    if not sa_json_obj:
        st.error("Falta el JSON de la cuenta de servicio. Cárgalo en *Secrets* o pégalo en la UI y guarda.")
        st.stop()

    sa_path = "service_account.json"
    with open(sa_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(sa_json_obj) if isinstance(sa_json_obj, dict) else sa_json_obj)

    gauth = GoogleAuth(settings={
        "client_config_backend": "service",
        "service_config": {"client_json_file_path": sa_path}
    })
    gauth.ServiceAuth()
    return GoogleDrive(gauth)

# --------------------- Drive helpers ---------------------
def list_pdfs_in_folder(drive: GoogleDrive, folder_id: str) -> List[Dict]:
    q = f"'{folder_id}' in parents and mimeType = 'application/pdf' and trashed = false"
    return [{"id": f["id"], "title": f["title"]} for f in drive.ListFile({"q": q}).GetList()]

def download_pdf_temp(drive: GoogleDrive, file_id: str) -> str:
    f = drive.CreateFile({"id": file_id})
    fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    f.GetContentFile(tmp_path)
    return tmp_path

def extract_text_from_pdf(path: str) -> str:
    try:
        return extract_text(path) or ""
    except Exception:
        return ""

# --------------------- Tabs funcionales ---------------------
tab1, tab2 = st.tabs(["🔎 Leer carpeta de Drive", "📤 Subir PDFs manualmente"])

with tab1:
    folder_input = st.text_input(
        "Pega la **URL** o el **ID** de la carpeta en Google Drive:",
        placeholder="https://drive.google.com/drive/folders/xxxxxxxxxxxxxxxxxxxxxxxx"
    )
    scan = st.button("🚀 Escanear PDFs", type="primary", use_container_width=True)

    if scan:
        folder_id = parse_folder_id_from_input(folder_input)
        if not folder_id:
            st.error("No pude detectar el **ID** de carpeta. Verifica la URL/ID.")
            st.stop()

        with st.spinner("Autenticando con Google Drive…"):
            drive = get_drive()

        with st.spinner("Listando PDFs…"):
            files = list_pdfs_in_folder(drive, folder_id)

        if not files:
            st.warning("No se encontraron PDFs en esa carpeta.")
        else:
            st.success(f"Encontrados {len(files)} PDFs. Escaneando…")
            prog = st.progress(0.0)
            rows = []
            for i, meta in enumerate(files, start=1):
                try:
                    tmp_pdf = download_pdf_temp(drive, meta["id"])
                    text = extract_text_from_pdf(tmp_pdf)

                    # Antes: solo validábamos 'venta dm'. Ahora Shopify también cae,
                    # porque la frase típica incluye 'venta dm'; mantenemos esta verificación
                    # para evitar procesar PDFs irrelevantes.
                    if "venta dm" not in normalize_text(text).lower() and "shopify" not in normalize_text(text).lower():
                        os.remove(tmp_pdf); prog.progress(i/len(files)); continue

                    venta_dm, folio = extract_fields_from_text(text)
                    rows.append({"archivo": meta["title"], "venta_dm": venta_dm, "folio": folio})
                    os.remove(tmp_pdf)
                except Exception:
                    rows.append({"archivo": meta.get("title", "(desconocido)"), "venta_dm": "", "folio": ""})
                prog.progress(i/len(files))

            df = pd.DataFrame(rows)
            if df.empty:
                st.warning("Se escanearon los PDFs pero ninguno contenía 'Venta DM' / 'Shopify'.")
            else:
                st.dataframe(df, use_container_width=True, height=420)
                out = io.BytesIO()
                with pd.ExcelWriter(out, engine="openpyxl") as w:
                    df.to_excel(w, index=False, sheet_name="Resultados")
                st.download_button(
                    "⬇️ Descargar Excel",
                    data=out.getvalue(),
                    file_name="venta_dm_folios.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )

with tab2:
    st.write("Procesa PDFs sin Google Drive.")
    files = st.file_uploader("Sube uno o más PDFs", type=["pdf"], accept_multiple_files=True)
    if files:
        rows = []
        for f in files:
            try:
                fd, tmp = tempfile.mkstemp(suffix=".pdf"); os.close(fd)
                with open(tmp, "wb") as h: h.write(f.read())
                text = extract_text_from_pdf(tmp)
                if ("venta dm" in normalize_text(text).lower()) or ("shopify" in normalize_text(text).lower()):
                    venta_dm, folio = extract_fields_from_text(text)
                    rows.append({"archivo": f.name, "venta_dm": venta_dm, "folio": folio})
                os.remove(tmp)
            except Exception:
                rows.append({"archivo": f.name, "venta_dm": "", "folio": ""})

        if rows:
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, height=360)
            out = io.BytesIO()
            with pd.ExcelWriter(out, engine="openpyxl") as w:
                df.to_excel(w, index=False, sheet_name="Resultados")
            st.download_button(
                "⬇️ Descargar Excel (subidos)",
                data=out.getvalue(),
                file_name="venta_dm_folios_subidos.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
