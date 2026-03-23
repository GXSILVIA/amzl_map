import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.plugins import Draw, MarkerCluster
from folium.features import DivIcon
import yaml
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth

# 1. CONFIGURACIÓN INICIAL
st.set_page_config(page_title="AMZL Hub - Localizador Pro", layout="wide")

if 'map_center' not in st.session_state:
    st.session_state.map_center = [19.4326, -99.1332]
if 'dibujos_manuales' not in st.session_state:
    st.session_state.dibujos_manuales = []

def obtener_color(v):
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
    authenticator = stauth.Authenticate(
        config['credentials'], config['cookie']['name'], 
        config['cookie']['key'], config['cookie']['expiry_days']
    )
except Exception as e:
    st.error(f"Error al cargar configuración: {e}")
    st.stop()

name, authentication_status, username = authenticator.login(location='main')

if authentication_status:
    col_mapa, col_controles = st.columns([3.5, 1.2]) 

    with col_controles:
        st.title(f"Bienvenido {name}")
        authenticator.logout('Cerrar Sesión', 'main')
        
        archivo = st.file_uploader("Sube Excel", type=["xlsx"])
        puntos_excel = pd.read_excel(archivo).dropna(subset=['Latitud', 'Longitud']) if archivo else pd.DataFrame()

        st.divider()
        st.subheader("🔍 Filtros (Excel)")
        mostrar_nombres = st.toggle("🏷️ Nombres", True)
        
        c1, c2 = st.columns(2)
        f_v = [c1.checkbox("⚪ R0", True), c1.checkbox("🟡 R1-15", True), c1.checkbox("🟠 R16-20", True),
               c2.checkbox("🔴 R21-30", True), c2.checkbox("🏮 R31-40", True), c2.checkbox("🍷 R40+", True)]
        activos = [i for i, v in enumerate(f_v) if v]

    with col_mapa:
        # Centrar mapa si hay datos nuevos
        if not puntos_excel.empty and 'cargado' not in st.session_state:
            st.session_state.map_center = [puntos_excel['Latitud'].mean(), puntos_excel['Longitud'].mean()]
            st.session_state.cargado = True
        
        # Crear Mapa Base
        m = folium.Map(location=st.session_state.map_center, zoom_start=12, control_scale=True)
        
        # 3. CAPA EXCEL CON CLUSTER
        if not puntos_excel.empty:
            mc = MarkerCluster(name="Agrupamiento").add_to(m)
            for _, fila in puntos_excel.iterrows():
                v = fila.get('Volumen', 0)
                rango = 0 if v==0 else 1 if v<=15 else 2 if v<=20 else 3 if v<=30 else 4 if v<=40 else 5
                
                if rango in activos:
                    lat, lon = fila['Latitud'], fila['Longitud']
                    radio = float(fila.get('Radio', 800))
                    
                    # Dibujar Radio (Círculo)
                    folium.Circle(
                        location=[lat, lon],
                        radius=radio,
                        color='black', weight=1, fill=True,
                        fill_color=obtener_color(v), fill_opacity=0.4,
                    ).add_to(m)
                    
                    # Añadir al Cluster (Marcador invisible o punto)
                    folium.Marker(
                        location=[lat, lon],
                        popup=f"Zona: {fila['Nombre']}",
                        icon=folium.Icon(color='lightgray', icon='info-sign') if not mostrar_nombres else None
                    ).add_to(mc)

                    if mostrar_nombres:
                        folium.Marker(
                            [lat, lon], 
                            icon=DivIcon(html=f'<div style="font-size: 8pt; color: black; font-weight: bold; width:100px;">{fila["Nombre"]}</div>')
                        ).add_to(m)

        # 4. HERRAMIENTA DE DIBUJO
        Draw(export=False, position='topleft', draw_options={
            'circle': {'showRadius': True, 'metric': True, 'shapeOptions': {'color': '#0000FF'}},
            'polyline': False, 'rectangle': False, 'polygon': False, 'marker': False
        }).add_to(m)

        # Renderizar
        map_output = st_folium(m, width="100%", height=700, key="mapa_v1")

    # 5. PERSISTENCIA DE DATOS DIBUJADOS
    if map_output and map_output.get("all_drawings"):
        datos_temp = []
        for i, dibujo in enumerate(map_output["all_drawings"]):
            props = dibujo.get('properties')
            geom = dibujo.get('geometry')
            if props and 'radius' in props:
                datos_temp.append({
                    "Nombre": f"Manual_{i+1}",
                    "Latitud": geom['coordinates'][1],
                    "Longitud": geom['coordinates'][0],
                    "Radio_m": round(props['radius'], 1)
                })
        st.session_state.dibujos_manuales = datos_temp

    # EXPORTACIÓN
    if st.session_state.dibujos_manuales:
        with col_controles:
            st.subheader("💾 Exportar")
            df_exp = pd.DataFrame(st.session_state.dibujos_manuales)
            st.download_button("📥 Descargar CSV", df_exp.to_csv(index=False).encode('utf-8'), "zonas.csv")
            st.dataframe(df_exp, height=150)

elif authentication_status is False:
    st.error('Usuario/Contraseña incorrectos')
elif authentication_status is None:
    st.warning('Ingresa tus credenciales')
