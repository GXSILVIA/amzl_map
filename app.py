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

if 'puntos_datos' not in st.session_state:
    st.session_state.puntos_datos = pd.DataFrame(columns=['Nombre', 'Latitud', 'Longitud', 'Radio', 'Volumen'])
if 'map_center' not in st.session_state:
    st.session_state.map_center = [19.4326, -99.1332]
if 'map_zoom' not in st.session_state:
    st.session_state.map_zoom = 12

def asignar_rango(v):
    try:
        v = float(v)
        if v == 0: return 0
        if v <= 15: return 1
        if v <= 20: return 2
        if v <= 30: return 3
        if v <= 40: return 4
        return 5
    except: return 0

# 2. AUTENTICACIÓN
try:
    with open('config.yaml') as file:
        config = yaml.load(file, Loader=SafeLoader)
    authenticator = stauth.Authenticate(config['credentials'], config['cookie']['name'], config['cookie']['key'], config['cookie']['expiry_days'])
    authenticator.login(location='main')
except Exception as e:
    st.error(f"Error config: {e}"); st.stop()

if st.session_state.get("authentication_status"):
    col_mapa, col_controles = st.columns([3, 1])

    with col_controles:
        st.title("Panel de Control")
        authenticator.logout('Cerrar Sesión', 'main')
        
        # CARGA DE ARCHIVO
        archivo = st.file_uploader("Sube Excel (Lat/Lon)", type=["xlsx"])
        if archivo and st.button("🚀 Cargar Datos"):
            df_raw = pd.read_excel(archivo)
            df_raw.columns = df_raw.columns.str.strip()
            renombrar = {'lat':'Latitud','latitud':'Latitud','lon':'Longitud','longitud':'Longitud','radio':'Radio','volumen':'Volumen'}
            st.session_state.puntos_datos = df_raw.rename(columns=renombrar).dropna(subset=['Latitud', 'Longitud'])
            if not st.session_state.puntos_datos.empty:
                st.session_state.map_center = [st.session_state.puntos_datos['Latitud'].mean(), st.session_state.puntos_datos['Longitud'].mean()]
            st.rerun()

        # --- FILTROS DE RANGO (BOTONES/CHECKBOXES) ---
        st.subheader("🔍 Filtros de Rango")
        f0 = st.checkbox("⚪ R0 (Cero)", value=True)
        f1 = st.checkbox("🟡 R1-15", value=True)
        f2 = st.checkbox("🟠 R16-20", value=True)
        f3 = st.checkbox("🔴 R21-30", value=True)
        f4 = st.checkbox("🏮 R31-40", value=True)
        f5 = st.checkbox("🍷 R40+", value=True)
        filtros_activos = [i for i, check in enumerate([f0, f1, f2, f3, f4, f5]) if check]

        # --- GESTIÓN Y EDICIÓN ---
        st.subheader("📍 Editor de Puntos")
        # Aquí puedes mover el lugar (lat/lon) y tamaño (radio) y se actualiza la lista
        edited_df = st.data_editor(st.session_state.puntos_datos, num_rows="dynamic", key="editor_final_v13")
        if not edited_df.equals(st.session_state.puntos_datos):
            st.session_state.puntos_datos = edited_df
            st.rerun()

        c1, c2 = st.columns(2)
        if c1.button("➕ Agregar Punto"):
            nueva = pd.DataFrame([{'Nombre': f'P_{len(st.session_state.puntos_datos)+1}', 'Latitud': st.session_state.map_center[0], 'Longitud': st.session_state.map_center[1], 'Radio': 800, 'Volumen': 0}])
            st.session_state.puntos_datos = pd.concat([st.session_state.puntos_datos, nueva], ignore_index=True)
            st.rerun()
        if c2.button("🗑️ Borrar Todo"):
            st.session_state.puntos_datos = pd.DataFrame(columns=['Nombre', 'Latitud', 'Longitud', 'Radio', 'Volumen'])
            st.rerun()

        mostrar_nombres = st.toggle("🏷️ Mostrar Nombres", value=True)

    with col_mapa:
        m = folium.Map(location=st.session_state.map_center, zoom_start=st.session_state.map_zoom)
        
        if not st.session_state.puntos_datos.empty:
            df_m = st.session_state.puntos_datos.copy()
            # Asegurar que los datos sean numéricos para evitar errores
            df_m['Radio'] = pd.to_numeric(df_m['Radio'], errors='coerce').fillna(800)
            df_m['Volumen'] = pd.to_numeric(df_m['Volumen'], errors='coerce').fillna(0)
            df_m['rango_id'] = df_m['Volumen'].apply(asignar_rango)
            
            # Filtrar puntos según los checkboxes
            df_filtrado = df_m[df_m['rango_id'].isin(filtros_activos)]

            for _, fila in df_filtrado.iterrows():
                colores_map = {0:"#FFFFFF", 1:"#FFFF00", 2:"#FFA500", 3:"#FF7777", 4:"#FF0000", 5:"#800000"}
                color_v = colores_map.get(fila['rango_id'], "#888")
                
                folium.Circle(
                    [fila['Latitud'], fila['Longitud']], 
                    radius=float(fila['Radio']), 
                    color="black", weight=1, fill=True, fill_color=color_v, fill_opacity=0.6,
                    popup=f"<b>{fila['Nombre']}</b><br>Vol: {fila['Volumen']}<br>Radio: {fila['Radio']}m"
                ).add_to(m)
                
                if mostrar_nombres:
                    folium.Marker(
                        [fila['Latitud'], fila['Longitud']], 
                        icon=DivIcon(html=f'<div style="font-size: 9pt; color: black; font-weight: normal; width:150px; text-shadow: 1px 1px white;">{fila["Nombre"]}</div>')
                    ).add_to(m)

        # Renderizado del mapa. Usamos 'returned_objects' vacío para evitar el parpadeo al mover.
        st_folium(m, width="100%", height=850, key="mapa_estable_v13", returned_objects=[])

elif st.session_state.get("authentication_status") is False:
    st.error('Credenciales incorrectas.')
