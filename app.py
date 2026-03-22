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

# Inicializar estados de sesión para persistencia y estabilidad
if 'puntos_datos' not in st.session_state:
    st.session_state.puntos_datos = pd.DataFrame(columns=['Nombre', 'Latitud', 'Longitud', 'Radio', 'Volumen'])
if 'map_center' not in st.session_state:
    st.session_state.map_center = [19.4326, -99.1332]
if 'map_zoom' not in st.session_state:
    st.session_state.map_zoom = 12

searcher_main = ArcGIS(timeout=15)

def geolocalizar(df_input):
    df = df_input.copy()
    lats, lons = [], []
    progreso = st.progress(0)
    for i, fila in df.iterrows():
        try:
            loc = searcher_main.geocode(f"{fila.get('CP', '')}, {fila.get('Nombre', '')}, Mexico")
            lats.append(loc.latitude if loc else None)
            lons.append(loc.longitude if loc else None)
        except: lats.append(None); lons.append(None)
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
    st.error(f"Error de configuración: {e}")
    st.stop()

if st.session_state.get("authentication_status"):
    col_mapa, col_controles = st.columns([3, 1])

    with col_controles:
        st.title("Panel de Control")
        authenticator.logout('Cerrar Sesión', 'main')
        
        # --- SECCIÓN DE CARGA ---
        st.subheader("📁 Cargar Datos")
        modo = st.radio("Entrada por:", ["Coordenadas", "Código Postal"])
        archivo = st.file_uploader("Excel (.xlsx)", type=["xlsx"])
        
        if archivo and st.button("🚀 Cargar y Dibujar"):
            df_raw = pd.read_excel(archivo)
            df_raw.columns = df_raw.columns.str.strip()
            if modo == "Código Postal":
                df_new = geolocalizar(df_raw)
            else:
                renombrar = {'lat':'Latitud','latitud':'Latitud','lon':'Longitud','longitud':'Longitud','radio':'Radio','volumen':'Volumen'}
                df_new = df_raw.rename(columns=renombrar)
            
            st.session_state.puntos_datos = pd.concat([st.session_state.puntos_datos, df_new], ignore_index=True)
            if not df_new.empty:
                st.session_state.map_center = [df_new['Latitud'].dropna().iloc[0], df_new['Longitud'].dropna().iloc[0]]
            st.rerun()

        # --- SECCIÓN DE GESTIÓN ---
        st.subheader("📍 Editar Puntos")
        
        # Botón para Borrar Todo
        if st.button("🗑️ Borrar Todos los Puntos", type="secondary"):
            st.session_state.puntos_datos = pd.DataFrame(columns=['Nombre', 'Latitud', 'Longitud', 'Radio', 'Volumen'])
            st.rerun()

        # Editor de tabla interactiva
        edited_df = st.data_editor(st.session_state.puntos_datos, num_rows="dynamic", key="editor_v3")
        if not edited_df.equals(st.session_state.puntos_datos):
            st.session_state.puntos_datos = edited_df
            st.rerun()

        if not st.session_state.puntos_datos.empty:
            buf = io.BytesIO()
            st.session_state.puntos_datos.to_excel(buf, index=False)
            st.download_button("📥 Exportar Excel Final", buf, "mapa_amzl.xlsx")

    with col_mapa:
        # Dibujo del mapa estable
        m = folium.Map(location=st.session_state.map_center, zoom_start=st.session_state.map_zoom)
        folium.LatLngPopup().add_to(m)

        df_draw = st.session_state.puntos_datos.dropna(subset=['Latitud', 'Longitud'])
        
        for i, fila in df_draw.iterrows():
            vol = float(fila.get('Volumen', 0))
            # Escala de colores según volumen
            color = "#800" if vol > 40 else "#F00" if vol > 30 else "#F77" if vol > 20 else "#FFA500" if vol > 15 else "#FF0" if vol > 0 else "#FFF"
            rad = float(fila.get('Radio', 800))

            folium.Circle(
                [fila['Latitud'], fila['Longitud']], 
                radius=rad, color="black", weight=1, fill=True, fill_color=color, fill_opacity=0.6,
                popup=f"<b>{fila.get('Nombre','')}</b><br>Radio: {rad}m<br>Vol: {vol}"
            ).add_to(m)
            
            folium.Marker(
                [fila['Latitud'], fila['Longitud']], 
                icon=DivIcon(html=f'<div style="font-size: 8pt; font-weight: bold; color: black; text-shadow: 1px 1px white; width:120px">{fila.get("Nombre","")}</div>')
            ).add_to(m)

        # Captura de interacción y mantenimiento de vista
        output = st_folium(m, width="100%", height=800, key="mapa_final", returned_objects=["last_clicked", "center", "zoom"])

        if output:
            # Mantener el mapa donde el usuario lo movió
            if output.get("center"):
                st.session_state.map_center = [output["center"]["lat"], output["center"]["lng"]]
            if output.get("zoom"):
                st.session_state.map_zoom = output["zoom"]

            # Agregar punto nuevo al hacer clic
            clic = output.get("last_clicked")
            if clic:
                # Evitar que el clic se repita infinitamente en la recarga
                clic_id = f"{clic['lat']}_{clic['lng']}"
                if 'ultimo_clic_id' not in st.session_state or st.session_state.ultimo_clic_id != clic_id:
                    st.session_state.ultimo_clic_id = clic_id
                    nuevo_p = {'Nombre': f'Punto_{len(st.session_state.puntos_datos)+1}', 'Latitud': clic['lat'], 'Longitud': clic['lng'], 'Radio': 800, 'Volumen': 0}
                    st.session_state.puntos_datos = pd.concat([st.session_state.puntos_datos, pd.DataFrame([nuevo_p])], ignore_index=True)
                    st.rerun()

elif st.session_state.get("authentication_status") is False:
    st.error('Credenciales incorrectas.')
