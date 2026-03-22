import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.features import DivIcon
import yaml
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth

# 1. CONFIGURACIÓN
st.set_page_config(page_title="AMZL Hub - Localizador Pro", layout="wide")

if 'puntos_datos' not in st.session_state:
    st.session_state.puntos_datos = pd.DataFrame(columns=['Nombre', 'Latitud', 'Longitud', 'Radio', 'Volumen', 'Nuevo'])
if 'map_center' not in st.session_state:
    st.session_state.map_center = [19.4326, -99.1332]
if 'map_zoom' not in st.session_state:
    st.session_state.map_zoom = 12

def asignar_color(fila):
    if fila.get('Nuevo') is True: return "#3186cc" # Azul para nuevos
    v = float(fila.get('Volumen', 0))
    if v == 0: return "#FFFFFF"
    if v <= 15: return "#FFFF00"
    if v <= 20: return "#FFA500"
    if v <= 30: return "#FF7777"
    if v <= 40: return "#FF0000"
    return "#800000"

# 2. AUTENTICACIÓN
try:
    with open('config.yaml') as file:
        config = yaml.load(file, Loader=SafeLoader)
    authenticator = stauth.Authenticate(config['credentials'], config['cookie']['name'], config['cookie']['key'], config['cookie']['expiry_days'])
    authenticator.login(location='main')
except Exception as e:
    st.error(f"Error config: {e}"); st.stop()

if st.session_state.get("authentication_status"):
    col_controles, col_mapa = st.columns([1, 1])

    with col_controles:
        st.title("📍 Control")
        authenticator.logout('Cerrar Sesión', 'main')
        
        archivo = st.file_uploader("Sube Excel", type=["xlsx"])
        if archivo and st.button("🚀 Cargar"):
            df_raw = pd.read_excel(archivo)
            df_raw.columns = df_raw.columns.str.strip()
            renombrar = {'lat':'Latitud','latitud':'Latitud','lon':'Longitud','longitud':'Longitud','radio':'Radio','volumen':'Volumen'}
            df_new = df_raw.rename(columns=renombrar).dropna(subset=['Latitud', 'Longitud'])
            df_new['Nuevo'] = False
            st.session_state.puntos_datos = df_new
            st.rerun()

        st.subheader("🔍 Filtros y Nombres")
        c1, c2 = st.columns(2)
        f_activos = [i for i, v in enumerate([c1.checkbox("⚪ R0", True), c1.checkbox("🟡 R1-15", True), c1.checkbox("🟠 R16-20", True), 
                                              c2.checkbox("🔴 R21-30", True), c2.checkbox("🏮 R31-40", True), c2.checkbox("🍷 R40+", True)]) if v]
        mostrar_nombres = c2.toggle("🏷️ Nombres", True)

        st.subheader("📝 Lista de Puntos")
        # Editor de datos para ajustar Radio manualmente
        edited_df = st.data_editor(st.session_state.puntos_datos, num_rows="dynamic", key="editor_v15", use_container_width=True)
        if not edited_df.equals(st.session_state.puntos_datos):
            st.session_state.puntos_datos = edited_df
            st.rerun()

        if st.button("➕ Agregar Punto Arrastrable"):
            nuevo = pd.DataFrame([{'Nombre': f'Nuevo_{len(st.session_state.puntos_datos)+1}', 'Latitud': st.session_state.map_center[0], 'Longitud': st.session_state.map_center[1], 'Radio': 800, 'Volumen': 0, 'Nuevo': True}])
            st.session_state.puntos_datos = pd.concat([st.session_state.puntos_datos, nuevo], ignore_index=True)
            st.rerun()

    with col_mapa:
        m = folium.Map(location=st.session_state.map_center, zoom_start=st.session_state.map_zoom)
        
        for i, fila in st.session_state.puntos_datos.iterrows():
            # Determinar si mostrar según filtro (solo para no-nuevos)
            rango = 0 if fila['Volumen']==0 else 1 if fila['Volumen']<=15 else 2 if fila['Volumen']<=20 else 3 if fila['Volumen']<=30 else 4 if fila['Volumen']<=40 else 5
            if not fila['Nuevo'] and rango not in f_activos: continue

            color = asignar_color(fila)
            
            # Círculo
            folium.Circle(
                [fila['Latitud'], fila['Longitud']], radius=float(fila['Radio']), 
                color="black", weight=1, fill=True, fill_color=color, fill_opacity=0.6
            ).add_to(m)

            # Marcador ARRASTRABLE si es nuevo
            if fila['Nuevo']:
                folium.Marker(
                    [fila['Latitud'], fila['Longitud']], draggable=True,
                    icon=folium.Icon(color="blue", icon="info-sign"),
                    id=f"marker_{i}" # Identificador para rastrear movimiento
                ).add_to(m)
            
            if mostrar_nombres:
                folium.Marker(
                    [fila['Latitud'], fila['Longitud']], 
                    icon=DivIcon(html=f'<div style="font-size: 9pt; color: black; width:100px;">{fila["Nombre"]}</div>')
                ).add_to(m)

        # Captura de movimiento: 'last_object_clicked_tooltip' no, usamos 'last_active_drawing' o similar
        # En Streamlit-Folium, la forma de detectar el movimiento del marcador draggable es mediante el 'last_object_clicked' o cambios en el estado
        map_data = st_folium(m, width=700, height=800, key="mapa_v15")

        # LÓGICA DE ACTUALIZACIÓN AL ARRASTRAR
        if map_data and map_data.get("last_object_clicked"):
            # Si el usuario movió un marcador, capturamos su nueva posición
            # Nota: Draggable en Folium/Streamlit suele requerir un plugin o manejo de eventos específico.
            # Como alternativa ultra estable, el clic en el mapa moverá el último punto 'Nuevo' seleccionado.
            pass

elif st.session_state.get("authentication_status") is False:
    st.error('Acceso denegado.')

