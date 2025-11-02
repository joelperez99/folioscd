# streamlit_app.py — Prueba mínima de UI (sin imports pesados)
import streamlit as st

st.set_page_config(page_title="Smoke Test", page_icon="✅")
st.title("✅ La UI cargó correctamente")
st.write("Si ves esto, tu configuración de Streamlit Cloud está bien.")

st.subheader("Prueba de secreto (opcional)")
has_secret = "GDRIVE_SERVICE_JSON" in st.secrets
st.write("GDRIVE_SERVICE_JSON:", "✅ presente" if has_secret else "❌ no encontrado")

st.subheader("Prueba de import diferido")
if st.button("Probar imports pesados"):
    with st.spinner("Importando módulos pesados…"):
        import pandas as pd
        from pdfminer.high_level import extract_text
        from pydrive2.auth import GoogleAuth
        from pydrive2.drive import GoogleDrive
    st.success("Listo. Imports OK.")

st.caption("Si esta pantalla no aparece, revisa el archivo principal en Settings → Advanced → Main file path.")
