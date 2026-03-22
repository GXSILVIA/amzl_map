import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.features import DivIcon
import yaml
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth
from geopy.geocoders import ArcGIS, Nominatim
import io

# 1. CONFIGURACIÓN DE PÁGINA
st.set_page_config(page_title="AMZL Hub - Localizador Pro", layout="wide")

# Inicializar estados de sesión para estabilidad
if 'puntos_datos' not in st.session_state:
    st.session_state.puntos_datos = pd.DataFrame(columns=['Nombre', 'Latitud', 'Longitud', 'Radio', 'Volumen'])
if 'map_center' not in st.session_state:
    st.session_state.map_center = [19.4326, -99.1332]
if 'map_zoom' not in st.session_state:
    st.session_state.map_zoom = 12
if 'ultimo_clic' not in st.session_state:
    st.session_state.ultimo_clic = None

searcher_main = ArcGIS(timeout=15)
searcher_backup = Nominatim(user_agent="amzl_hub_mx_v20")

def geolocalizar_con_precision(df_input):
    df = df_input.copy()
    if 'CP' in df.columns:
        df['CP'] = df['CP'].astype(str).str.strip().str.replace('.0', '', regex=False).str.zfill(5)
    
    lats, lons = [], []
    progreso = st.progress(0)
    for i, fila in df.iterrows():
        cp, nombre = str(fila.get('CP', '')), str(fila.get('Nombre', ''))
        try:
            loc = searcher_main.geocode(f"{cp}, {nombre}, Mexico")
            if not loc: loc = searcher_backup.geocode(query={"postalcode": cp, "country": "Mexico"})
            lats.append(loc.latitude if loc else None)
            lons.append(loc.longitude if loc else None)
        except: 
            lats.append(None); lons.append(None)
        progreso.progress((i + 1) / len(df))
    df['Latitud'], df['Longitud'] = lats, lons
    progreso.empty()
    return df

# 2. AUTENTICACIÓN
try:
    with open('config.yaml') as file:
        config = yaml.load(file, Loader=SafeLoader)
    authenticator = stauth.Authenticate(config['credentials'], config['cookie']['name'], config['cookie']['key'], config['cookie']['expiry_days'])
    authenticator.login(location='main')
except Exception as e:
    st.error(f"Error con config.yaml: {e}")
    st.stop()

if st.session_state.get("authentication_status"):
    col_mapa, col_controles = st.columns([3, 1])

    with col_controles:
        st.title("Panel de Control")
        authenticator.logout('Cerrar Sesión', 'main')
        st.markdown("---")
        
        modo = st.radio("Modo de entrada:", ["Coordenadas", "Código Postal"])
        archivo = st.file_uploader("Sube tu archivo Excel", type=["xlsx"])
        
        if archivo and st.button("Cargar datos de Excel"):
            df_raw = pd.read_excel(archivo)
            df_raw.columns = df_raw.columns.str.strip()
            
            if modo == "Código Postal":
                df_procesado = geolocalizar_con_precision(df_raw)
            else:
                renombrar = {'lat':'Latitud','latitud':'Latitud','lon':'Longitud','longitud':'Longitud','lng':'Longitud','radio':'Radio','volumen':'Volumen'}
                df_procesado = df_raw.rename(columns=renombrar)
            
            st.session_state.puntos_datos = pd.concat([st.session_state.puntos_datos, df_procesado], ignore_index=True)
            st.rerun()

        st.subheader("📍 Editor de Puntos")
        # Tabla interactiva para mover puntos o cambiar radios
        edited_df = st.data_editor(st.session_state.puntos_datos, num_rows="dynamic", key="editor_puntos")
        if not edited_df.equals(st.session_state.puntos_datos):
            st.session_state.puntos_datos = edited_df
            st.rerun()

        # Exportación
        if not st.session_state.puntos_datos.empty:
            buffer = io.BytesIO()
            st.session_state.puntos_datos.to_excel(buffer, index=False, engine='openpyxl')
            st.download_button("📥 Descargar Excel Actualizado", buffer, "mapa_exportado.xlsx")

        mostrar_nombres = st.checkbox("🏷️ Mostrar Nombres en Mapa", value=True)

    with col_mapa:
        # Definir centro dinámico
        df_ver = st.session_state.puntos_datos.dropna(subset=['Latitud', 'Longitud'])
        
        m = folium.Map(location=st.session_state.map_center, zoom_start=st.session_state.map_zoom)
        folium.LatLngPopup().add_to(m)

        for i, fila in df_ver.iterrows():
            # Asignar color por volumen (opcional)
            v = float(fila.get('Volumen', 0))
            color = "#FF0000" if v > 30 else "#FFA500" if v > 15 else "#3186cc"
            rad = float(fila.get('Radio', 800))

            folium.Circle(
                [fila['Latitud'], fila['Longitud']], 
                radius=rad, color="black", weight=1, fill=True, fill_color=color, fill_opacity=0.5,
                popup=f"ID: {i}<br><b>Nombre:</b> {fila.get('Nombre','')}<br><b>Radio:</b> {rad}m"
            ).add_to(m)
            
            if mostrar_nombres:
                folium.Marker(
                    [fila['Latitud'], fila['Longitud']], 
                    icon=DivIcon(html=f'<div style="font-size: 8pt; font-weight: bold; color: black; text-shadow: 1px 1px white;">{fila.get("Nombre","")}</div>')
                ).add_to(m)

        # Captura de interacción con el mapa
        map_output = st_folium(m, width="100%", height=750, key="mapa_v2", returned_objects=["last_clicked", "center", "zoom"])

        # 1. Guardar posición para estabilidad
        if map_output:
            if map_output.get("center"):
                st.session_state.map_center = [map_output["center"]["lat"], map_output["center"]["lng"]]
            if map_output.get("zoom"):
                st.session_state.map_zoom = map_output["zoom"]

            # 2. Lógica de clic para nuevo punto (evitar duplicidad)
            if map_output.get("last_clicked"):
                clic_actual = map_output["last_clicked"]
                if clic_actual != st.session_state.ultimo_clic:
                    st.session_state.ultimo_clic = clic_actual
                    nuevo_reg = {
                        'Nombre': f"Nuevo_{len(st.session_state.puntos_datos)+1}",
                        'Latitud': clic_actual['lat'], 'Longitud': clic_actual['lng'],
                        'Radio': 800, 'Volumen': 0
                    }
                    st.session_state.puntos_datos = pd.concat([st.session_state.puntos_datos, pd.DataFrame([nuevo_reg])], ignore_index=True)
                    st.rerun()

elif st.session_state.get("authentication_status") is False:
    st.error('Usuario/Contraseña incorrectos')
elif st.session_state.get("authentication_status") is None:
    st.warning('Por favor, ingrese sus credenciales')
