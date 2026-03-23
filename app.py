import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.plugins import Draw
from folium.features import DivIcon
import yaml
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth

# 1. CONFIGURACIÓN
st.set_page_config(page_title="AMZL Hub - Localizador Pro", layout="wide")

# CSS para superponer la tabla sobre el mapa
st.markdown("""
    <style>
    .overlay-table {
        position: absolute; top: 10px; right: 10px; z-index: 1000;
        background: rgba(255, 255, 255, 0.9); padding: 10px;
        border-radius: 5px; border: 1px solid #ccc; max-width: 300px;
    }
    </style>
""", unsafe_allow_html=True)

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
    st.error(f"Error config: {e}"); st.stop()

name, authentication_status, username = authenticator.login(location='main')

if authentication_status:
    col_mapa, col_controles = st.columns([3.5, 1.2]) 

    with col_controles:
        st.title(f"Hola, {name}")
        authenticator.logout('Cerrar Sesión', 'main')
        archivo = st.file_uploader("Cargar Excel", type=["xlsx"])
        puntos_excel = pd.read_excel(archivo).dropna(subset=['Latitud', 'Longitud']) if archivo else pd.DataFrame()
        
        st.divider()
        mostrar_nombres = st.toggle("🏷️ Mostrar Nombres", True)
        
        c1, c2 = st.columns(2)
        f_v = [c1.checkbox("⚪ R0", True), c1.checkbox("🟡 R1-15", True), c1.checkbox("🟠 R16-20", True),
               c2.checkbox("🔴 R21-30", True), c2.checkbox("🏮 R31-40", True), c2.checkbox("🍷 R40+", True)]
        activos = [i for i, v in enumerate(f_v) if v]

    with col_mapa:
        # Contenedor para la tabla flotante
        placeholder = st.empty()
        
        if not puntos_excel.empty and 'cargado' not in st.session_state:
            st.session_state.map_center = [puntos_excel['Latitud'].mean(), puntos_excel['Longitud'].mean()]
            st.session_state.cargado = True
        
        m = folium.Map(location=st.session_state.map_center, zoom_start=12)
        
        # Grupo para nombres (permite ocultarlos dinámicamente)
        grupo_nombres = folium.FeatureGroup(name="Nombres")
        
        if not puntos_excel.empty:
            for _, fila in puntos_excel.iterrows():
                v = fila.get('Volumen', 0)
                rango = 0 if v==0 else 1 if v<=15 else 2 if v<=20 else 3 if v<=30 else 4 if v<=40 else 5
                
                if rango in activos:
                    lat, lon = fila['Latitud'], fila['Longitud']
                    folium.Circle(
                        location=[lat, lon], radius=float(fila.get('Radio', 800)),
                        color='black', weight=1, fill=True, fill_color=obtener_color(v), fill_opacity=0.4,
                        tooltip=f"{fila['Nombre']} | Vol: {v}"
                    ).add_to(m)

                    if mostrar_nombres:
                        folium.Marker(
                            [lat, lon], 
                            icon=DivIcon(html=f'<div style="font-size: 8pt; font-weight: bold; color: black; width:150px;">{fila["Nombre"]}</div>')
                        ).add_to(grupo_nombres)
            
            if mostrar_nombres:
                grupo_nombres.add_to(m)

        Draw(export=False, position='topleft', draw_options={
            'circle': {'showRadius': True, 'metric': True, 'shapeOptions': {'color': '#0000FF'}},
            'polyline': False, 'rectangle': False, 'polygon': False, 'marker': False, 'circlemarker': False
        }).add_to(m)

        map_output = st_folium(m, width="100%", height=750, key="map_v3")

    # 3. PROCESAR DIBUJOS MANUALES Y MOSTRAR TABLA SOBRE EL MAPA
    if map_output and map_output.get("all_drawings"):
        datos_temp = []
        for i, dibujo in enumerate(map_output["all_drawings"]):
            props = dibujo.get('properties')
            geom = dibujo.get('geometry')
            if props and 'radius' in props:
                lng, lat = geom['coordinates']
                datos_temp.append({
                    "ID": f"NUEVO_{i+1}",
                    "Lat": round(lat, 5),
                    "Lon": round(lng, 5),
                    "Radio_m": round(props['radius'], 1)
                })
        
        if datos_temp:
            df_new = pd.DataFrame(datos_temp)
            # Inyectar la tabla sobre el mapa usando el placeholder
            with placeholder.container():
                st.markdown('<div class="overlay-table"><b>Nuevos Radios</b>', unsafe_allow_html=True)
                st.dataframe(df_new, height=150, hide_index=True)
                st.download_button("📥 Descargar CSV", df_new.to_csv(index=False).encode('utf-8'), "zonas.csv", use_container_width=True)
                st.markdown('</div>', unsafe_allow_html=True)

elif authentication_status is False:
    st.error('Credenciales incorrectas')
