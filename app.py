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

# Estados para evitar parpadeos y pérdida de posición
if 'map_center' not in st.session_state:
    st.session_state.map_center = [19.4326, -99.1332]
if 'map_zoom' not in st.session_state:
    st.session_state.map_zoom = 12
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
        
        # --- FILTROS ARRIBA ---
        st.subheader("🔍 Filtros de Rango")
        labels = ["⚪ R0", "🟡 R1-15", "🟠 R16-20", "🔴 R21-30", "🏮 R31-40", "🍷 R40+"]
        
        # Usamos columnas para que ocupen menos espacio arriba
        f_checks = []
        c1, c2 = st.columns(2)
        for i in range(6):
            target = c1 if i < 3 else c2
            f_checks.append(target.checkbox(labels[i], value=True, key=f"f_{i}"))

        st.markdown("---")
        
        # --- CARGA DE ARCHIVO ---
        archivo = st.file_uploader("Sube tu Excel", type=["xlsx"])
        
        if archivo:
            # Solo procesamos si es un archivo nuevo para evitar el bucle de refresco
            if st.session_state.get('ultimo_archivo_nombre') != archivo.name:
                df_raw = pd.read_excel(archivo)
                df_raw.columns = df_raw.columns.str.strip()
                
                # Normalizar columnas y asegurar 'Volumen'
                renombrar = {'lat':'Latitud','latitud':'Latitud','lon':'Longitud','longitud':'Longitud','lng':'Longitud'}
                df_proc = df_raw.rename(columns=renombrar).dropna(subset=['Latitud', 'Longitud'])
                if 'Volumen' not in df_proc.columns: df_proc['Volumen'] = 0
                
                # CENTRADO AUTOMÁTICO: Solo ocurre aquí al cargar el archivo
                st.session_state.map_center = [df_proc['Latitud'].mean(), df_proc['Longitud'].mean()]
                st.session_state.map_zoom = 12
                st.session_state.df_final = df_proc
                st.session_state.ultimo_archivo_nombre = archivo.name
                st.rerun()

        mostrar_nombres = st.toggle("🏷️ Ver Nombres", True)

    with col_mapa:
        if st.session_state.df_final is not None:
            df = st.session_state.df_final.copy()
            
            # Lógica de rangos
            def asignar_rango(v):
                try:
                    v = float(v) if pd.notnull(v) else 0
                    return 0 if v==0 else 1 if v<=15 else 2 if v<=20 else 3 if v<=30 else 4 if v<=40 else 5
                except: return 0
            
            df['rango_id'] = df['Volumen'].apply(asignar_rango)
            filtros_activos = [i for i, v in enumerate(f_checks) if v]
            df_filtrado = df[df['rango_id'].isin(filtros_activos)]

            # Crear el mapa con la posición guardada
            m = folium.Map(
                location=st.session_state.map_center, 
                zoom_start=st.session_state.map_zoom,
                control_scale=True
            )

            # Dibujar puntos
            for _, fila in df_filtrado.iterrows():
                color = {0:"#FFF", 1:"#FF0", 2:"#FFA500", 3:"#F77", 4:"#F00", 5:"#800"}.get(fila['rango_id'], "#888")
                
                folium.Circle(
                    [fila['Latitud'], fila['Longitud']], 
                    radius=float(fila.get('Radio', 800)),
                    color="black", weight=1, fill=True, fill_color=color, fill_opacity=0.6,
                    tooltip=f"Vol: {fila['Volumen']}"
                ).add_to(m)
                
                if mostrar_nombres:
                    folium.Marker(
                        [fila['Latitud'], fila['Longitud']], 
                        icon=DivIcon(html=f'<div style="font-size: 8pt; font-weight: bold; text-shadow: 1px 1px white; width: 100px;">{fila.get("Nombre","")}</div>')
                    ).add_to(m)

            # RENDERIZADO: Usamos use_container_width y retornamos solo lo necesario
            # IMPORTANTE: No capturamos el centro aquí para evitar el parpadeo infinito
            st_folium(
                m, 
                width=1200, # Ajuste fijo para estabilidad
                height=750, 
                key="mapa_estático"
            )
        else:
            st.info("Esperando archivo Excel para centrar el mapa...")

elif st.session_state.get("authentication_status") is False:
    st.error('Credenciales incorrectas')
