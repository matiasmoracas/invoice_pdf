import streamlit as st
from streamlit_drawable_canvas import st_canvas
from PIL import Image
import io
import re
import fitz  # PyMuPDF
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import datetime

st.set_page_config(page_title="Firmas Facturas Ingefix", layout="centered")
st.title("Gestor de firmas de Facturas Ingefix")

# ----------------------------------------------------------------------
# ======================= RUT: helpers =========================
# ----------------------------------------------------------------------
def _clean_rut(rut: str) -> str:
    """Quita todo excepto dígitos y K/k; devuelve en minúsculas."""
    return re.sub(r"[^0-9kK]", "", (rut or "")).lower()

def _calc_dv(num: str) -> str:
    """Calcula dígito verificador usando módulo 11 (pesos 2..7)."""
    s = 0
    mult = 2
    for d in reversed(num):
        s += int(d) * mult
        mult = 2 if mult == 7 else mult + 1
    r = 11 - (s % 11)
    if r == 11:
        return "0"
    if r == 10:
        return "k"
    return str(r)

def _format_miles(cuerpo: str) -> str:
    """Agrega puntos de miles al cuerpo del RUT."""
    if not cuerpo:
        return ""
    partes = []
    while len(cuerpo) > 3:
        partes.insert(0, cuerpo[-3:])
        cuerpo = cuerpo[:-3]
    if cuerpo:
        partes.insert(0, cuerpo)
    return ".".join(partes)

def format_rut(rut: str) -> str:
    """Devuelve el RUT con puntos y guion (ej: 12.345.678-9)."""
    rut = _clean_rut(rut)
    if not rut:
        return ""
    if len(rut) == 1:
        # Solo DV o un dígito aún; no formatear
        return rut
    cuerpo, dv = rut[:-1], rut[-1]
    if not cuerpo.isdigit():
        return rut  # incompleto; no formatear aún
    return f"{_format_miles(cuerpo)}-{dv}"

def validate_rut(rut: str) -> bool:
    """Valida el RUT con su DV."""
    rut = _clean_rut(rut)
    if len(rut) < 2 or not rut[:-1].isdigit():
        return False
    cuerpo, dv = rut[:-1], rut[-1]
    return _calc_dv(cuerpo) == dv

def rut_on_change():
    """Callback: toma lo escrito y lo deja formateado en vivo."""
    raw = st.session_state.get("rut_raw", "")
    formatted = format_rut(raw)
    st.session_state["rut"] = formatted
    st.session_state["rut_raw"] = formatted

# ----------------------------------------------------------------------
# Subir PDF (FACTURA)
# ----------------------------------------------------------------------
pdf_file = st.file_uploader("Sube la Factura (PDF)", type=["pdf"])

# ========== FORMULARIO CLIENTE ==========
with st.expander("🧾 Formulario Cliente", expanded=True):
    nombre = st.text_input("Nombre / Razón Social")
    recinto = st.text_input("Dirección / Recinto")
    fecha = st.date_input("Fecha", value=datetime.date.today())
    fecha_str = fecha.strftime("%d-%m-%Y")

    # RUT con formateo automático
    st.text_input("RUT", key="rut_raw", on_change=rut_on_change, placeholder="12.345.678-9")
    rut = st.session_state.get("rut", st.session_state.get("rut_raw", ""))

    # Mensaje de validación (opcional)
    if rut and not validate_rut(rut):
        st.caption("⚠️ RUT inválido (revisa dígito verificador).")

# ---------- Helper: extraer Nº de Factura del PDF ----------
def extraer_numero_factura(pdf_bytes):
    """
    Busca patrones como: 'Nº 123456', 'N°123456', 'No 123456', 'Nro 123456'.
    Devuelve el número (solo dígitos) o None.
    """
    patron = re.compile(r"(?:Nº|N°|No|N\.o|Nro\.?)\s*([0-9]{5,8})", re.IGNORECASE)
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page in doc:
            texto = page.get_text()
            m = patron.search(texto)
            if m:
                doc.close()
                return m.group(1)
        doc.close()
    except Exception:
        pass
    return None


# Guardamos bytes y detectamos Nº de factura ANTES de crear el input
pdf_bytes = None
if pdf_file is not None:
    pdf_bytes = pdf_file.read()
    numero_detectado = extraer_numero_factura(pdf_bytes)
    if numero_detectado:
        st.session_state["numero_factura"] = numero_detectado

# ========== DATOS DE FIRMA ==========
with st.expander("✍️ Datos de Vendedor Mesón", expanded=True):
    observacion = st.text_area("Observación (opcional)")
    iniciales_firmante = st.selectbox(
        "Iniciales del Vendedor Mesón",
        ["FVM", "JSC",],
        help="Puedes ajustar esta lista según los firmantes frecuentes."
    )
    numero_factura = st.text_input(
        "Número de la Factura",
        value=st.session_state.get("numero_factura", ""),
        key="numero_factura"
    )
    nombre_pdf = f"Factura {numero_factura} {iniciales_firmante}".strip()

# ================= FUNCIÓN PARA MODIFICAR EL PDF ====================
def insertar_firma_y_texto_en_pdf(
    pdf_bytes,
    firma_img,
    nombre,
    recinto,
    fecha_str,
    rut,
    observacion,
    firma_width=120
):
    """
    Inserta firma y textos en la factura.
    Usa los mismos campos que la guía:
    'Nombre:', 'Recinto:', 'RUT:', 'Fecha:', 'Firma', 'CEDIBLE'
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pagina = doc[-1]  # última página

    def insertar_dato_campo(etiqueta, texto, offset_x=5, offset_y=4):
        resultados = pagina.search_for(etiqueta)
        if resultados and texto:
            box = resultados[0]
            x = box.x1 + offset_x
            y = box.y0 + offset_y
            pagina.insert_text(
                (x, y),
                texto,
                fontsize=11,
                fontname="helv",
                fill=(0, 0, 0)
            )

    # Campos (mismos que en las guías)
    insertar_dato_campo("Nombre:", nombre, offset_x=15, offset_y=4)
    insertar_dato_campo("Recinto:", recinto, offset_x=15, offset_y=7)
    insertar_dato_campo("RUT:", rut, offset_x=5, offset_y=4)
    insertar_dato_campo("Fecha:", fecha_str, offset_x=20, offset_y=8)

    # Firma (del canvas)
    firma_box = pagina.search_for("Firma")
    if firma_box:
        rect = firma_box[0]
        x = rect.x0 + 10
        y = rect.y0 - 20
        img_bytes = io.BytesIO()
        firma_img.save(img_bytes, format='PNG')
        img_bytes = img_bytes.getvalue()
        image = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
        w_orig, h_orig = image.size
        escala = firma_width / w_orig
        h_escala = h_orig * escala
        firma_rect = fitz.Rect(x, y, x + firma_width, y + h_escala)
        pagina.insert_image(firma_rect, stream=img_bytes)

    # Observación (opcional) debajo de "CEDIBLE"
    cedible_box = pagina.search_for("CEDIBLE")
    if cedible_box and observacion.strip():
        cbox = cedible_box[0]
        page_width = pagina.rect.width
        y_obs = cbox.y1 + 10

        texto_label = "Observación:"
        ancho_label = fitz.get_text_length(texto_label, fontsize=11, fontname="helv")
        ancho_campo = 280
        alto_campo = 45
        espacio = 10
        total_ancho = ancho_label + espacio + ancho_campo
        x_inicio = (page_width - total_ancho) / 2

        pagina.insert_text(
            (x_inicio, y_obs + 5),
            texto_label,
            fontsize=11,
            fontname="helv",
            fill=(0, 0, 0)
        )

        textbox_rect = fitz.Rect(
            x_inicio + ancho_label + espacio, y_obs,
            x_inicio + ancho_label + espacio + ancho_campo, y_obs + alto_campo
        )
        pagina.draw_rect(textbox_rect, color=(0, 0, 0), width=0.5)
        pagina.insert_textbox(
            textbox_rect,
            observacion.strip(),
            fontsize=10,
            fontname="helv",
            align=0,
            fill=(0, 0, 0)
        )

    # Salida
    output = io.BytesIO()
    doc.save(output)
    doc.close()
    output.seek(0)
    return output

def render_preview(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pagina = doc[-1]
    zoom = 4
    mat = fitz.Matrix(zoom, zoom)
    pix = pagina.get_pixmap(matrix=mat)
    img_data = pix.tobytes("png")
    doc.close()
    return img_data

def subir_a_drive(nombre_archivo, contenido_pdf):
    creds_info = st.secrets["gcp_service_account"]
    credentials = service_account.Credentials.from_service_account_info(creds_info)
    servicio = build("drive", "v3", credentials=credentials)
    file_metadata = {
        "name": nombre_archivo,
        "mimeType": "application/pdf",
        # MISMA CARPETA COMPARTIDA (puedes cambiarla si quieres una carpeta solo para facturas)
        "parents": ["0ALdPR-m3f2zlUk9PVA"]
    }
    contenido_pdf.seek(0)
    media = MediaIoBaseUpload(contenido_pdf, mimetype="application/pdf")
    archivo = servicio.files().create(
        body=file_metadata,
        media_body=media,
        fields="id",
        supportsAllDrives=True
    ).execute()
    return archivo.get("id")

# ================= UI PRINCIPAL ====================
if pdf_bytes is not None:
    st.subheader("Vista previa del documento original:")
    st.image(render_preview(pdf_bytes), use_container_width=True)
    
    st.subheader("Dibuja la firma aquí:")

col1, col2, col3 = st.columns([1, 2, 1])

with col2:
    canvas_result = st_canvas(
        fill_color="rgba(0, 0, 0, 0)",
        stroke_width=2,
        stroke_color="black",
        background_color="#ffffff00",
        height=180,
        width=450,
        drawing_mode="freedraw",
        key="canvas"
    )


    signature_img = None
    if canvas_result.image_data is not None:
        signature_img = Image.fromarray((canvas_result.image_data).astype("uint8"))

    if st.button("Firmar Factura"):
        # Validaciones
        if signature_img is None:
            st.warning("⚠️ Dibuja la firma primero.")
        elif not (nombre and recinto and fecha and rut and st.session_state.get("numero_factura", "")):
            st.warning("⚠️ Completa todos los campos del formulario.")
        elif not validate_rut(rut):
            st.warning("⚠️ El RUT no es válido.")
        else:
            # Construir PDF final
            pdf_final_io = insertar_firma_y_texto_en_pdf(
                pdf_bytes=pdf_bytes,
                firma_img=signature_img,
                nombre=nombre,
                recinto=recinto,
                fecha_str=fecha_str,
                rut=rut,
                observacion=observacion,
                firma_width=120,
            )

            if pdf_final_io:
                st.success("✅ Factura firmada correctamente.")
                with st.spinner("Subiendo a Google Drive..."):
                    subir_a_drive(
                        f"Factura {st.session_state['numero_factura']} {iniciales_firmante}.pdf",
                        pdf_final_io
                    )
                st.success("Factura enviada a Google Drive con éxito.")

                st.subheader("Vista previa del documento final:")
                st.image(render_preview(pdf_final_io.getvalue()), use_container_width=True)

                st.download_button(
                    label="Descargar Factura Firmada",
                    data=pdf_final_io,
                    file_name=f"Factura {st.session_state['numero_factura']} {iniciales_firmante}.pdf",
                    mime="application/pdf"
                )

st.markdown("""
---
<center style='color: gray;'>Desarrollado por Ingefix 2025</center>
""", unsafe_allow_html=True)
