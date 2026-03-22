import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.features import DivIcon
import yaml
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth
import io

# 1. CONFIGURACIÓN INICIAL
st.set_page_config(page_title="AMZL Hub - Localizador Pro", layout="wide")

if 'puntos_datos' not in st.session_state:
    st.session_state.puntos_datos = pd.DataFrame(columns=['Nombre', 'Latitud', 'Longitud', 'Radio', 'Volumen'])
if 'map_center' not in st.session_state:
    st.session_state.map_center = [19.4326, -99.1332]
if 'map_zoom' not in st.session_state:
    st.session_state.map_zoom = 12

# 2. AUTENTICACIÓN
try:
    with open('config.yaml') as file:
        config = yaml.load(file, Loader=SafeLoader)
    authenticator = stauth.Authenticate(config['credentials'], config['cookie']['name'], config['cookie']['key'], config['cookie']['expiry_days'])
    authenticator.login(location='main')
except Exception as e:
    st.error(f"Error de configuración: {e}"); st.stop()

if st.session_state.get("authentication_status"):
    col_mapa, col_controles = st.columns([3, 1])

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

        st.subheader("📍 Gestión de Puntos")
        
        # TABLA EDITABLE
        edited_df = st.data_editor(st.session_state.puntos_datos, num_rows="dynamic", key="editor_estable_v9")
        if not edited_df.equals(st.session_state.puntos_datos):
            st.session_state.puntos_datos = edited_df
            st.rerun()

        # BOTONES DE ACCIÓN (Única forma de agregar/borrar)
        c1, c2 = st.columns(2)
        if c1.button("➕ Agregar Punto"):
            nueva_fila = pd.DataFrame([{'Nombre': f'P_{len(st.session_state.puntos_datos)+1}', 'Latitud': st.session_state.map_center[0], 'Longitud': st.session_state.map_center[1], 'Radio': 800, 'Volumen': 0}])
            st.session_state.puntos_datos = pd.concat([st.session_state.puntos_datos, nueva_fila], ignore_index=True)
            st.rerun()
        
        if c2.button("🗑️ Borrar Todo"):
            st.session_state.puntos_datos = pd.DataFrame(columns=['Nombre', 'Latitud', 'Longitud', 'Radio', 'Volumen'])
            st.rerun()

        # --- NUEVA FUNCIÓN: UBICAR EN EL MAPA ---
        st.subheader("🔍 Ubicar Punto")
        nombres_lista = st.session_state.puntos_datos['Nombre'].tolist()
        punto_seleccionado = st.selectbox("Selecciona un nombre para ir a su ubicación:", ["-- Seleccionar --"] + nombres_lista)
        
        if punto_seleccionado != "-- Seleccionar --":
            fila_sel = st.session_state.puntos_datos[st.session_state.puntos_datos['Nombre'] == punto_seleccionado].iloc[0]
            st.session_state.map_center = [fila_sel['Latitud'], fila_sel['Longitud']]
            st.session_state.map_zoom = 15 # Zoom más cercano para ubicarlo bien
            # Nota: No hacemos rerun aquí para evitar parpadeo, el mapa lo tomará al redibujarse

        mostrar_nombres = st.toggle("🏷️ Mostrar Nombres", value=True)

    with col_mapa:
        if not st.session_state.puntos_datos.empty:
            m = folium.Map(location=st.session_state.map_center, zoom_start=st.session_state.map_zoom)
            
            # Limpieza para evitar TypeError
            df_mapa = st.session_state.puntos_datos.copy()
            df_mapa['Radio'] = pd.to_numeric(df_mapa['Radio'], errors='coerce').fillna(800)
            df_mapa['Latitud'] = pd.to_numeric(df_mapa['Latitud'], errors='coerce')
            df_mapa['Longitud'] = pd.to_numeric(df_mapa['Longitud'], errors='coerce')
            df_mapa = df_mapa.dropna(subset=['Latitud', 'Longitud'])

            for _, fila in df_mapa.iterrows():
                # Círculo
                folium.Circle(
                    [fila['Latitud'], fila['Longitud']], 
                    radius=float(fila['Radio']), 
                    color="black", weight=1, fill=True, fill_color="#3186cc", fill_opacity=0.4,
                    popup=f"Nombre: {fila['Nombre']}"
                ).add_to(m)
                
                # Nombre (Texto normal, NO negritas)
                if mostrar_nombres:
                    folium.Marker(
                        [fila['Latitud'], fila['Longitud']], 
                        icon=DivIcon(html=f'<div style="font-size: 9pt; color: black; font-weight: normal; width:150px; text-shadow: 1px 1px white;">{fila["Nombre"]}</div>')
                    ).add_to(m)

            # Renderizado estable
            output = st_folium(m, width="100%", height=850, key="mapa_final_v9", returned_objects=["center", "zoom"])

            # Guardar posición para estabilidad (Solo si el usuario mueve el mapa manualmente)
            if output:
                if output.get("center"):
                    st.session_state.map_center = [output["center"]["lat"], output["center"]["lng"]]
                if output.get("zoom"):
                    st.session_state.map_zoom = output["zoom"]
        else:
            st.info("Sube un archivo o agrega un punto para comenzar.")

elif st.session_state.get("authentication_status") is False:
    st.error('Credenciales incorrectas.')
