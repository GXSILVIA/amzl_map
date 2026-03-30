import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.features import DivIcon
import yaml
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth
import io

# 1. CONFIGURACIÓN Y PERSISTENCIA
st.set_page_config(page_title="AMZL Hub - Localizador Pro", layout="wide")

# Inicializamos estados para que el mapa no "salte"
if 'map_center' not in st.session_state:
    st.session_state.map_center = [19.4326, -99.1332]
if 'map_zoom' not in st.session_state:
    st.session_state.map_zoom = 12
if 'df_final' not in st.session_state:
    st.session_state.df_final = None

# 2. AUTENTICACIÓN (Simplificada para el ejemplo)
try:
    with open('config.yaml') as file:
        config = yaml.load(file, Loader=SafeLoader)
    authenticator = stauth.Authenticate(config['credentials'], config['cookie']['name'], config['cookie']['key'], config['cookie']['expiry_days'])
    authenticator.login(location='main')
except: st.error("Error config.yaml"); st.stop()

if st.session_state.get("authentication_status"):
    col_mapa, col_controles = st.columns([3, 1])

    with col_controles:
        st.title("Panel de Control")
        authenticator.logout('Cerrar Sesión', 'main')
        
        # --- FILTROS EN LA PARTE SUPERIOR DEL PANEL ---
        st.subheader("🔍 Filtros de Rango")
        labels = ["⚪ R0", "🟡 R1-15", "🟠 R16-20", "🔴 R21-30", "🏮 R31-40", "🍷 R40+"]
        f_checks = [st.checkbox(labels[i], value=True, key=f"f_{i}") for i in range(6)]
        
        st.markdown("---")
        archivo = st.file_uploader("Sube tu Excel", type=["xlsx"])
        
        if archivo and st.session_state.df_final is None:
            df_raw = pd.read_excel(archivo)
            df_raw.columns = df_raw.columns.str.strip()
            # Asegurar columna Volumen
            if 'Volumen' not in df_raw.columns: df_raw['Volumen'] = 0
            
            # Renombrar coordenadas si vienen en minúsculas
            renombrar = {'lat':'Latitud','latitud':'Latitud','lon':'Longitud','longitud':'Longitud','lng':'Longitud'}
            df_proc = df_raw.rename(columns=renombrar).dropna(subset=['Latitud', 'Longitud'])
            
            # REGLA: Abrir el mapa donde están los datos nuevos
            st.session_state.map_center = [df_proc['Latitud'].mean(), df_proc['Longitud'].mean()]
            st.session_state.df_final = df_proc
            st.rerun()

    with col_mapa:
        if st.session_state.df_final is not None:
            df = st.session_state.df_final.copy()
            
            # Asignación de rangos
            def asignar_rango(v):
                v = float(v) if pd.notnull(v) else 0
                return 0 if v==0 else 1 if v<=15 else 2 if v<=20 else 3 if v<=30 else 4 if v<=40 else 5
            
            df['rango_id'] = df['Volumen'].apply(asignar_rango)
            filtros_activos = [i for i, v in enumerate(f_checks) if v]
            df_filtrado = df[df['rango_id'].isin(filtros_activos)]

            # Crear mapa usando el estado persistente
            m = folium.Map(
                location=st.session_state.map_center, 
                zoom_start=st.session_state.map_zoom
            )

            for _, fila in df_filtrado.iterrows():
                color = {0:"#FFF", 1:"#FF0", 2:"#FFA500", 3:"#F77", 4:"#F00", 5:"#800"}.get(fila['rango_id'])
                folium.Circle(
                    [fila['Latitud'], fila['Longitud']], 
                    radius=float(fila.get('Radio', 800)),
                    color="black", weight=1, fill=True, fill_color=color, fill_opacity=0.6,
                    popup=f"Volumen: {fila['Volumen']}"
                ).add_to(m)

            # RENDERIZADO INTELIGENTE (Captura zoom y centro sin recargar todo)
            map_output = st_folium(
                m, 
                width="100%", 
                height=750, 
                key="mapa_vFinal",
                returned_objects=["center", "zoom"] # Solo pedimos lo necesario
            )

            # Actualizamos el estado solo si el usuario movió el mapa manualmente
            if map_output:
                if map_output.get("center"):
                    st.session_state.map_center = [map_output["center"]["lat"], map_output["center"]["lng"]]
                if map_output.get("zoom"):
                    st.session_state.map_zoom = map_output["zoom"]
        else:
            st.info("Carga un archivo Excel para visualizar el mapa.")
