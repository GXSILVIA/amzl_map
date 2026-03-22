import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.features import DivIcon
import yaml
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth
from geopy.geocoders import ArcGIS
import io

# 1. CONFIGURACIÓN
st.set_page_config(page_title="AMZL Hub - Localizador Pro", layout="wide")

if 'puntos_datos' not in st.session_state:
    st.session_state.puntos_datos = pd.DataFrame(columns=['Nombre', 'Latitud', 'Longitud', 'Radio', 'Volumen'])
if 'map_center' not in st.session_state:
    st.session_state.map_center = [19.4326, -99.1332]
if 'map_zoom' not in st.session_state:
    st.session_state.map_zoom = 12

searcher_main = ArcGIS(timeout=15)

def asignar_rango(v):
    try:
        v = float(v)
        return 0 if v==0 else 1 if v<=15 else 2 if v<=20 else 3 if v<=30 else 4 if v<=40 else 5
    except: return 0

# 2. AUTENTICACIÓN
try:
    with open('config.yaml') as file:
        config = yaml.load(file, Loader=SafeLoader)
    authenticator = stauth.Authenticate(config['credentials'], config['cookie']['name'], config['cookie']['key'], config['cookie']['expiry_days'])
    authenticator.login(location='main')
except Exception as e:
    st.error(f"Error de config: {e}"); st.stop()

if st.session_state.get("authentication_status"):
    col_mapa, col_controles = st.columns([3, 1])

    with col_controles:
        st.title("Panel de Control")
        authenticator.logout('Cerrar Sesión', 'main')
        
        # --- CARGA ---
        archivo = st.file_uploader("Sube tu Excel (.xlsx)", type=["xlsx"])
        if archivo and st.button("🚀 Cargar y Limpiar"):
            df_raw = pd.read_excel(archivo)
            df_raw.columns = df_raw.columns.str.strip()
            renombrar = {'lat':'Latitud','latitud':'Latitud','lon':'Longitud','longitud':'Longitud','radio':'Radio','volumen':'Volumen'}
            df_new = df_raw.rename(columns=renombrar).dropna(subset=['Latitud', 'Longitud'])
            st.session_state.puntos_datos = df_new
            if not df_new.empty:
                st.session_state.map_center = [df_new['Latitud'].mean(), df_new['Longitud'].mean()]
            st.rerun()

        # --- FILTROS Y VISIBILIDAD ---
        st.subheader("🔍 Filtros y Nombres")
        mostrar_nombres = st.toggle("🏷️ Mostrar Nombres", value=True)
        
        # Filtros de volumen
        c1, c2 = st.columns(2)
        f0 = c1.checkbox("⚪ R0", value=True)
        f1 = c1.checkbox("🟡 R1-15", value=True)
        f2 = c1.checkbox("🟠 R16-20", value=True)
        f3 = c2.checkbox("🔴 R21-30", value=True)
        f4 = c2.checkbox("🏮 R31-40", value=True)
        f5 = c2.checkbox("🍷 R40+", value=True)
        filtros_activos = [i for i, check in enumerate([f0, f1, f2, f3, f4, f5]) if check]

        # --- ACTUALIZACIÓN Y EDICIÓN ---
        st.subheader("⚙️ Gestión")
        if st.button("🔄 Actualizar / Refrescar Mapa"):
            st.rerun()
            
        edited_df = st.data_editor(st.session_state.puntos_datos, num_rows="dynamic", key="editor_final")
        if not edited_df.equals(st.session_state.puntos_datos):
            st.session_state.puntos_datos = edited_df
            # No hacemos rerun automático aquí para que el usuario termine de editar

    with col_mapa:
        if not st.session_state.puntos_datos.empty:
            df_temp = st.session_state.puntos_datos.copy()
            df_temp['rango_id'] = df_temp['Volumen'].apply(asignar_rango)
            # Aplicar filtro a TODO (círculos y nombres)
            df_filtrado = df_temp[df_temp['rango_id'].isin(filtros_activos)]

            m = folium.Map(location=st.session_state.map_center, zoom_start=st.session_state.map_zoom)
            
            for _, fila in df_filtrado.iterrows():
                colores_map = {0:"#FFF", 1:"#FF0", 2:"#FFA500", 3:"#FF7777", 4:"#F00", 5:"#800"}
                color = colores_map.get(fila['rango_id'], "#888")
                
                # Círculo
                folium.Circle(
                    [fila['Latitud'], fila['Longitud']], 
                    radius=float(fila.get('Radio', 800)), 
                    color="black", weight=1, fill=True, fill_color=color, fill_opacity=0.6,
                    popup=f"Nombre: {fila['Nombre']}<br>Vol: {fila['Volumen']}"
                ).add_to(m)
                
                # Nombre (Solo si el toggle está activo)
                if mostrar_nombres:
                    folium.Marker(
                        [fila['Latitud'], fila['Longitud']], 
                        icon=DivIcon(html=f'<div style="font-size: 9pt; font-weight: 800; color: black; width:150px; text-align: center;">{fila["Nombre"]}</div>')
                    ).add_to(m)

            output = st_folium(m, width="100%", height=850, key="mapa_v6", returned_objects=["last_clicked", "center", "zoom"])

            if output:
                # Actualizar posición silenciosamente
                st.session_state.map_center = [output["center"]["lat"], output["center"]["lng"]]
                st.session_state.map_zoom = output["zoom"]

                # Clic para nuevo punto
                clic = output.get("last_clicked")
                if clic:
                    cid = f"{clic['lat']}_{clic['lng']}"
                    if st.session_state.get('last_clic_id') != cid:
                        st.session_state.last_clic_id = cid
                        nuevo = {'Nombre': f'P_{len(st.session_state.puntos_datos)+1}', 'Latitud': clic['lat'], 'Longitud': clic['lng'], 'Radio': 800, 'Volumen': 0}
                        st.session_state.puntos_datos = pd.concat([st.session_state.puntos_datos, pd.DataFrame([nuevo])], ignore_index=True)
                        st.rerun()
        else:
            st.info("👋 Sube un archivo Excel para activar el mapa.")

elif st.session_state.get("authentication_status") is False:
    st.error('Credenciales incorrectas.')
