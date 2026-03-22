import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.features import DivIcon
import yaml
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth
import io

# 1. CONFIGURACIÓN
st.set_page_config(page_title="AMZL Hub - Localizador Pro", layout="wide")

# Inicializar estados de sesión
if 'puntos_datos' not in st.session_state:
    st.session_state.puntos_datos = pd.DataFrame(columns=['Nombre', 'Latitud', 'Longitud', 'Radio', 'Volumen'])
if 'map_center' not in st.session_state:
    st.session_state.map_center = [19.4326, -99.1332]
if 'map_zoom' not in st.session_state:
    st.session_state.map_zoom = 12

def asignar_color(v):
    try:
        v = float(v)
        if v == 0: return "#FFFFFF"
        if v <= 15: return "#FFFF00"
        if v <= 20: return "#FFA500"
        if v <= 30: return "#FF7777"
        if v <= 40: return "#FF0000"
        return "#800000"
    except: return "#888888"

# 2. AUTENTICACIÓN
try:
    with open('config.yaml') as file:
        config = yaml.load(file, Loader=SafeLoader)
    authenticator = stauth.Authenticate(config['credentials'], config['cookie']['name'], config['cookie']['key'], config['cookie']['expiry_days'])
    authenticator.login(location='main')
except Exception as e:
    st.error(f"Error de configuración: {e}"); st.stop()

if st.session_state.get("authentication_status"):
    col_mapa, col_controles = st.columns([3, 1]) # Mapa más grande

    with col_controles:
        st.title("Panel de Control")
        authenticator.logout('Cerrar Sesión', 'main')
        
        archivo = st.file_uploader("Sube tu Excel (.xlsx)", type=["xlsx"])
        if archivo and st.button("🚀 Cargar Archivo"):
            df_raw = pd.read_excel(archivo)
            df_raw.columns = df_raw.columns.str.strip()
            renombrar = {'lat':'Latitud','latitud':'Latitud','lon':'Longitud','longitud':'Longitud','radio':'Radio','volumen':'Volumen'}
            st.session_state.puntos_datos = df_raw.rename(columns=renombrar).dropna(subset=['Latitud', 'Longitud'])
            if not st.session_state.puntos_datos.empty:
                st.session_state.map_center = [st.session_state.puntos_datos['Latitud'].mean(), st.session_state.puntos_datos['Longitud'].mean()]
            st.rerun()

        st.subheader("📍 Gestión")
        edited_df = st.data_editor(st.session_state.puntos_datos, num_rows="dynamic", key="editor_v11")
        if not edited_df.equals(st.session_state.puntos_datos):
            st.session_state.puntos_datos = edited_df
            st.rerun()

        c1, c2 = st.columns(2)
        if c1.button("➕ Agregar Punto"):
            # Al agregar, capturamos el centro actual del mapa para no "saltar"
            nueva = pd.DataFrame([{'Nombre': f'P_{len(st.session_state.puntos_datos)+1}', 'Latitud': st.session_state.map_center[0], 'Longitud': st.session_state.map_center[1], 'Radio': 800, 'Volumen': 0}])
            st.session_state.puntos_datos = pd.concat([st.session_state.puntos_datos, nueva], ignore_index=True)
            st.rerun()
        
        if c2.button("🗑️ Borrar Todo"):
            st.session_state.puntos_datos = pd.DataFrame(columns=['Nombre', 'Latitud', 'Longitud', 'Radio', 'Volumen'])
            st.rerun()

        st.subheader("🔍 Ubicar en Mapa")
        nombres = st.session_state.puntos_datos['Nombre'].tolist()
        sel = st.selectbox("Ir a:", ["-- Seleccionar --"] + nombres)
        if sel != "-- Seleccionar --":
            punto = st.session_state.puntos_datos[st.session_state.puntos_datos['Nombre'] == sel].iloc[0]
            st.session_state.map_center = [punto['Latitud'], punto['Longitud']]
            st.session_state.map_zoom = 15
            # Quitamos el rerun aquí para evitar parpadeo; el mapa se centrará al redibujarse

        mostrar_nombres = st.toggle("🏷️ Mostrar Nombres", value=True)

    with col_mapa:
        # CREACIÓN DEL MAPA
        m = folium.Map(location=st.session_state.map_center, zoom_start=st.session_state.map_zoom)
        
        if not st.session_state.puntos_datos.empty:
            df_m = st.session_state.puntos_datos.copy()
            # Parche para el TypeError: asegurar que Radio, Lat y Lon sean números
            df_m['Radio'] = pd.to_numeric(df_m['Radio'], errors='coerce').fillna(800)
            df_m['Latitud'] = pd.to_numeric(df_m['Latitud'], errors='coerce')
            df_m['Longitud'] = pd.to_numeric(df_m['Longitud'], errors='coerce')
            df_m = df_m.dropna(subset=['Latitud', 'Longitud'])

            for _, fila in df_m.iterrows():
                color_circulo = asignar_color(fila.get('Volumen', 0))
                
                folium.Circle(
                    [fila['Latitud'], fila['Longitud']], 
                    radius=float(fila['Radio']), 
                    color="black", weight=1, fill=True, fill_color=color_circulo, fill_opacity=0.6,
                    popup=f"<b>{fila['Nombre']}</b>"
                ).add_to(m)
                
                if mostrar_nombres:
                    folium.Marker(
                        [fila['Latitud'], fila['Longitud']], 
                        icon=DivIcon(html=f'<div style="font-size: 9pt; color: black; font-weight: normal; width:150px; text-shadow: 1px 1px white;">{fila["Nombre"]}</div>')
                    ).add_to(m)

        # RENDERIZADO ULTRA ESTABLE
        # Eliminamos 'center' y 'zoom' de los objetos retornados para que no refresque al mover
        output = st_folium(
            m, 
            width="100%", 
            height=850, 
            key="mapa_estatico_v11", 
            returned_objects=[] # Esto detiene el parpadeo al navegar
        )

elif st.session_state.get("authentication_status") is False:
    st.error('Acceso denegado.')
