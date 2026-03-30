import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.features import DivIcon
import yaml
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth

# 1. CONFIGURACIÓN INICIAL
st.set_page_config(page_title="AMZL Hub - Localizador Pro", layout="wide")

# Inicializar estados para evitar que el mapa "salte" al mar
if 'map_center' not in st.session_state:
    st.session_state.map_center = [19.4326, -99.1332]
if 'map_zoom' not in st.session_state:
    st.session_state.map_zoom = 11
if 'df_final' not in st.session_state:
    st.session_state.df_final = None

# 2. AUTENTICACIÓN
try:
    with open('config.yaml') as file:
        config = yaml.load(file, Loader=SafeLoader)
    authenticator = stauth.Authenticate(
        config['credentials'], config['cookie']['name'], 
        config['cookie']['key'], config['cookie']['expiry_days']
    )
    authenticator.login(location='main')
except Exception as e:
    st.error(f"Error config.yaml: {e}")
    st.stop()

if st.session_state.get("authentication_status"):
    col_mapa, col_controles = st.columns([3.5, 1])

    with col_controles:
        st.title("📍 Panel")
        authenticator.logout('Cerrar Sesión', 'main')
        
        # --- FILTROS EN LA PARTE SUPERIOR ---
        st.subheader("🔍 Filtros de Rango")
        labels = ["⚪ R0", "🟡 R1-15", "🟠 R16-20", "🔴 R21-30", "🏮 R31-40", "🍷 R40+"]
        f_checks = []
        c1, c2 = st.columns(2)
        for i in range(6):
            target = c1 if i < 3 else c2
            f_checks.append(target.checkbox(labels[i], value=True, key=f"filt_{i}"))

        st.markdown("---")
        archivo = st.file_uploader("Sube tu Excel", type=["xlsx"])
        
        if archivo:
            # Solo centramos el mapa si es la primera vez que cargamos este archivo
            if st.session_state.get('last_file') != archivo.name:
                df_raw = pd.read_excel(archivo)
                df_raw.columns = df_raw.columns.str.strip()
                
                renombrar = {'lat':'Latitud','latitud':'Latitud','lon':'Longitud','longitud':'Longitud','lng':'Longitud'}
                df_proc = df_raw.rename(columns=renombrar).dropna(subset=['Latitud', 'Longitud'])
                
                if 'Volumen' not in df_proc.columns: df_proc['Volumen'] = 0
                
                # ACTUALIZAR CENTRO BASADO EN DATOS REALES
                st.session_state.map_center = [df_proc['Latitud'].mean(), df_proc['Longitud'].mean()]
                st.session_state.df_final = df_proc
                st.session_state.last_file = archivo.name
                st.rerun()

    with col_mapa:
        if st.session_state.df_final is not None:
            df = st.session_state.df_final.copy()
            
            def asignar_rango(v):
                try:
                    val = float(v)
                    return 0 if val==0 else 1 if val<=15 else 2 if val<=20 else 3 if val<=30 else 4 if val<=40 else 5
                except: return 0
            
            df['rango_id'] = df['Volumen'].apply(asignar_rango)
            activos = [i for i, v in enumerate(f_checks) if v]
            df_ver = df[df['rango_id'].isin(activos)]

            # Crear mapa con posición persistente (Evita que se vaya al mar)
            m = folium.Map(location=st.session_state.map_center, zoom_start=st.session_state.map_zoom)

            for _, fila in df_ver.iterrows():
                color_map = {0:"#FFF", 1:"#FF0", 2:"#FFA500", 3:"#F77", 4:"#F00", 5:"#800"}
                color = color_map.get(fila['rango_id'], "#888")
                
                folium.Circle(
                    [fila['Latitud'], fila['Longitud']], 
                    radius=float(fila.get('Radio', 800)),
                    color="black", weight=1, fill=True, fill_color=color, fill_opacity=0.6,
                    tooltip=f"Nombre: {fila.get('Nombre','')}"
                ).add_to(m)
                
                # NOMBRES EN NEGRO CON SOMBRA BLANCA PARA VISIBILIDAD
                folium.Marker(
                    [fila['Latitud'], fila['Longitud']], 
                    icon=DivIcon(html=f"""
                        <div style="
                            font-size: 9pt; 
                            font-weight: bold; 
                            color: black !important; 
                            text-shadow: 2px 2px 4px white, -2px -2px 4px white; 
                            width: 150px; 
                            text-align: center;">
                            {fila.get('Nombre','')}
                        </div>""")
                ).add_to(m)

            # Renderizado. IMPORTANTE: quitamos el "returned_objects" de center para que no parpadee
            st_folium(m, width="100%", height=700, key="mapa_v_final")
        else:
            st.info("Por favor, carga un archivo Excel para centrar el mapa en tus puntos.")

elif st.session_state.get("authentication_status") is False:
    st.error('Acceso denegado')
