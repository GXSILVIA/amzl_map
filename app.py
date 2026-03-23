import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.plugins import Draw
from folium.features import DivIcon
import yaml
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth

# 1. CONFIGURACIÓN INICIAL
st.set_page_config(page_title="AMZL Hub - Localizador Pro", layout="wide")

if 'map_center' not in st.session_state:
    st.session_state.map_center = [19.4326, -99.1332]
if 'map_zoom' not in st.session_state:
    st.session_state.map_zoom = 12

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
    authenticator = stauth.Authenticate(config['credentials'], config['cookie']['name'], config['cookie']['key'], config['cookie']['expiry_days'])
    authenticator.login(location='main')
except Exception as e:
    st.error(f"Error de config: {e}"); st.stop()

if st.session_state.get("authentication_status"):
    col_mapa, col_controles = st.columns([3.5, 1.2]) 

    with col_controles:
        st.title("📍 Control")
        authenticator.logout('Cerrar Sesión', 'main')
        
        archivo = st.file_uploader("Sube Excel", type=["xlsx"])
        puntos_excel = pd.DataFrame()
        if archivo:
            puntos_excel = pd.read_excel(archivo).dropna(subset=['Latitud', 'Longitud'])
            if not puntos_excel.empty:
                st.session_state.map_center = [puntos_excel['Latitud'].mean(), puntos_excel['Longitud'].mean()]

        st.subheader("🔍 Filtros Visuales")
        mostrar_nombres = st.toggle("🏷️ Mostrar Nombres", True)
        
        # Filtros de Volumen
        c1, c2 = st.columns(2)
        f_v = [c1.checkbox("⚪ R0", True), c1.checkbox("🟡 R1-15", True), c1.checkbox("🟠 R16-20", True),
               c2.checkbox("🔴 R21-30", True), c2.checkbox("🏮 R31-40", True), c2.checkbox("🍷 R40+", True)]
        activos = [i for i, v in enumerate(f_v) if v]

    with col_mapa:
        m = folium.Map(location=st.session_state.map_center, zoom_start=st.session_state.map_zoom)
        
        # GRUPOS DE CAPAS
        capa_fija = folium.FeatureGroup(name="Círculos Excel (Fijos)").add_to(m)
        capa_edit = folium.FeatureGroup(name="Capa de Edición (Dibujo)").add_to(m)

        # 3. DIBUJAR PUNTOS EXCEL (FIJOS)
        if not puntos_excel.empty:
            for _, fila in puntos_excel.iterrows():
                v = fila.get('Volumen', 0)
                rango = 0 if v==0 else 1 if v<=15 else 2 if v<=20 else 3 if v<=30 else 4 if v<=40 else 5
                
                if rango in activos:
                    color_fill = obtener_color(v)
                    folium.Circle(
                        location=[fila['Latitud'], fila['Longitud']],
                        radius=float(fila.get('Radio', 800)),
                        color='black', weight=1,
                        fill=True, fill_color=color_fill, fill_opacity=0.6,
                        popup=f"Base: {fila.get('Nombre')}"
                    ).add_to(capa_fija)

                    if mostrar_nombres:
                        folium.Marker(
                            [fila['Latitud'], fila['Longitud']], 
                            icon=DivIcon(html=f'<div style="font-size: 8pt; color: black; font-weight: bold; width:100px;">{fila["Nombre"]}</div>')
                        ).add_to(capa_fija)

        # 4. HERRAMIENTA DE DIBUJO (Muestra radio al editar)
        draw = Draw(
            export=False,
            position='topleft',
            draw_options={
                'polyline': False, 'rectangle': False, 'polygon': False, 'marker': False, 'circlemarker': False,
                'circle': {
                    'showRadius': True, # MUESTRA EL RADIO AL DIBUJAR/EDITAR
                    'metric': True,
                    'shapeOptions': {'color': '#0000FF', 'fillOpacity': 0.4}
                }
            },
            edit_options={'edit': True, 'remove': True}
        )
        draw.add_to(m)
        
        # Agregamos selector de capas
        folium.LayerControl().add_to(m)
        m.add_child(folium.LatLngPopup())

        # RENDERIZAR Y EVITAR ERROR DE DIBUJO VACÍO
        map_output = st_folium(m, width="100%", height=800, key="mapa_final_v2")

    # 5. PROCESAMIENTO SEGURO (SOLUCIÓN AL ERROR DE IMAGEN)
    if map_output and map_output.get("all_drawings") is not None:
        datos_nuevos = []
        for dibujo in map_output["all_drawings"]:
            # Validamos que el dibujo tenga geometría y propiedades antes de acceder
            geom = dibujo.get('geometry')
            props = dibujo.get('properties')
            if geom and geom.get('type') == 'Point' and props and 'radius' in props:
                lng, lat = geom['coordinates']
                rad = props['radius']
                datos_nuevos.append({
                    "Nombre": f"Manual_{len(datos_nuevos)+1}",
                    "Latitud": round(lat, 6), "Longitud": round(lng, 6), "Radio": round(rad, 1)
                })

        if datos_nuevos:
            with col_controles:
                st.subheader("💾 Exportar Cambios")
                df_export = pd.DataFrame(datos_nuevos)
                st.download_button("📥 Descargar CSV", df_export.to_csv(index=False).encode('utf-8'), "mis_puntos.csv", "text/csv")
                st.dataframe(df_export, height=150)

elif st.session_state.get("authentication_status") is False:
    st.error('Usuario/Contraseña incorrectos')
