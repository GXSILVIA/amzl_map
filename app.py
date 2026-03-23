import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.plugins import Draw
import yaml
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth

# 1. CONFIGURACIÓN DE PÁGINA
st.set_page_config(page_title="AMZL Hub - Localizador Pro", layout="wide")

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
    st.error(f"Error de config: {e}"); st.stop()

if st.session_state.get("authentication_status"):
    col_mapa, col_controles = st.columns([3, 1]) 

    with col_controles:
        st.title("📍 Control")
        authenticator.logout('Cerrar Sesión', 'main')
        
        # Carga de Excel (Capa fija)
        archivo = st.file_uploader("Sube Excel de referencia", type=["xlsx"])
        puntos_excel = pd.DataFrame()
        if archivo:
            puntos_excel = pd.read_excel(archivo).dropna(subset=['Latitud', 'Longitud'])
            st.session_state.map_center = [puntos_excel['Latitud'].mean(), puntos_excel['Longitud'].mean()]
            st.success(f"📌 {len(puntos_excel)} puntos base cargados (Fijos)")

        st.divider()
        st.info("Utiliza las herramientas del mapa para crear, mover o borrar círculos **azules**.")

    with col_mapa:
        # Crear mapa base
        m = folium.Map(location=st.session_state.map_center, zoom_start=st.session_state.map_zoom)

        # 1. Capa Fija (Excel): Se agregan como objetos simples de Folium (no editables)
        if not puntos_excel.empty:
            for _, fila in puntos_excel.iterrows():
                folium.Circle(
                    location=[fila['Latitud'], fila['Longitud']],
                    radius=float(fila.get('Radio', 800)),
                    color='crimson',
                    fill=True,
                    fill_opacity=0.3,
                    popup=f"Base: {fila.get('Nombre', 'Punto')}"
                ).add_to(m)

        # 2. Capa Interactiva (Draw): Solo para círculos nuevos/azules
        draw = Draw(
            export=False,
            position='topleft',
            draw_options={
                'polyline': False, 'rectangle': False, 'polygon': False, 'marker': False, 'circlemarker': False,
                'circle': {'shapeOptions': {'color': '#0000FF', 'fillOpacity': 0.5}}
            },
            edit_options={'edit': True, 'remove': True}
        )
        draw.add_to(m)
        m.add_child(folium.LatLngPopup()) # Ver coordenadas al clickear

        # Renderizar mapa
        map_output = st_folium(m, width="100%", height=750, key="mapa_v1")

    # 3. PROCESAMIENTO DE DATOS DIBUJADOS
    if map_output and "all_drawings" in map_output:
        datos_nuevos = []
        for dibujo in map_output["all_drawings"]:
            # Filtramos solo círculos creados con la herramienta Draw
            if dibujo['geometry']['type'] == 'Point' and 'radius' in dibujo['properties']:
                lng, lat = dibujo['geometry']['coordinates']
                rad = dibujo['properties']['radius']
                datos_nuevos.append({
                    "ID": f"Nuevo_{len(datos_nuevos)+1}",
                    "Latitud": round(lat, 6),
                    "Longitud": round(lng, 6),
                    "Radio": round(rad, 1)
                })

        if datos_nuevos:
            df_export = pd.DataFrame(datos_nuevos)
            with col_controles:
                st.subheader("💾 Exportar Azules")
                st.download_button(
                    "📥 Descargar CSV",
                    df_export.to_csv(index=False).encode('utf-8'),
                    "puntos_interactivos.csv",
                    "text/csv",
                    use_container_width=True
                )
                st.dataframe(df_export, height=200, use_container_width=True)

elif st.session_state.get("authentication_status") is False:
    st.error('Credenciales incorrectas.')
