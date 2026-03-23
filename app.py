import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.plugins import Draw
from folium.features import DivIcon
import yaml
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth

# 1. CONFIGURACIÓN Y ESTADO
st.set_page_config(page_title="AMZL Hub - Localizador Pro", layout="wide")

if 'dibujos_persistentes' not in st.session_state:
    st.session_state.dibujos_persistentes = []
if 'map_center' not in st.session_state:
    st.session_state.map_center = [19.4326, -99.1332]
if 'zoom_nivel' not in st.session_state:
    st.session_state.zoom_nivel = 12

# 2. AUTENTICACIÓN
try:
    with open('config.yaml') as file:
        config = yaml.load(file, Loader=SafeLoader)
    authenticator = stauth.Authenticate(
        config['credentials'], config['cookie']['name'], 
        config['cookie']['key'], config['cookie']['expiry_days']
    )
except: st.error("Falta config.yaml"); st.stop()

name, auth_status, username = authenticator.login(location='main')

if auth_status:
    col_mapa, col_controles = st.columns([3.8, 1.2]) 

    with col_controles:
        st.title(f"📍 Gestión")
        authenticator.logout('Cerrar Sesión', 'main')
        
        # --- SECCIÓN EXCEL ---
        st.subheader("📁 Datos Excel")
        archivo = st.file_uploader("Subir Archivo", type=["xlsx"], label_visibility="collapsed")
        
        if archivo:
            puntos_excel = pd.read_excel(archivo).dropna(subset=['Latitud', 'Longitud'])
            if 'ultimo_archivo' not in st.session_state or st.session_state.ultimo_archivo != archivo.name:
                st.session_state.map_center = [puntos_excel['Latitud'].mean(), puntos_excel['Longitud'].mean()]
                st.session_state.ultimo_archivo = archivo.name
                st.rerun()
        else:
            puntos_excel = pd.DataFrame()

        mostrar_nombres = st.toggle("🏷️ Ver Nombres", True)
        
        # --- SECCIÓN HERRAMIENTAS ---
        st.divider()
        st.subheader("🛠️ Herramientas de Capa")
        st.info("Usa los iconos del mapa (arriba izquierda) para: \n1. **Nuevo Círculo** (🔵)\n2. **Editar/Mover** (✏️)\n3. **Borrar** (🗑️)")
        
        if st.button("💾 Guardar Cambios Manuales", use_container_width=True, type="primary"):
            # La lógica de guardado se dispara al final del script mediante st_folium
            st.success("¡Cambios registrados!")
            st.rerun()

        if st.button("❌ Limpiar Todo", use_container_width=True):
            st.session_state.dibujos_persistentes = []
            st.rerun()

    with col_mapa:
        # Crear Mapa
        m = folium.Map(location=st.session_state.map_center, zoom_start=st.session_state.zoom_nivel)
        
        # JS PARA RADIO EN TIEMPO REAL AL EDITAR
        script_radio = """
        <script>
        function updateRadius(e) {
            var layer = e.layer;
            var radius = Math.round(layer.getRadius());
            layer.bindTooltip("Radio: " + radius + "m", {permanent: true, direction: 'center'}).openTooltip();
        }
        document.addEventListener('DOMContentLoaded', function() {
            var map = L.DomUtil.get('map');
            window.map_obj.on('draw:editresize', updateRadius);
        });
        </script>
        """
        m.get_root().html.add_child(folium.Element(script_radio))

        # CAPA EXCEL
        if not puntos_excel.empty:
            for _, fila in puntos_excel.iterrows():
                folium.Circle(
                    location=[fila['Latitud'], fila['Longitud']], radius=float(fila.get('Radio', 800)),
                    color='black', weight=1, fill=True, fill_color='#FF5733', fill_opacity=0.3
                ).add_to(m)
                if mostrar_nombres:
                    folium.Marker([fila['Latitud'], fila['Longitud']], 
                        icon=DivIcon(html=f'<div style="font-size: 8pt; font-weight: bold; width:150px;">{fila["Nombre"]}</div>')).add_to(m)

        # CAPA MANUAL (PERSISTENTE)
        for d in st.session_state.dibujos_persistentes:
            folium.Circle(
                location=[d['lat'], d['lon']], radius=d['radius'],
                color='blue', weight=2, fill=True, fill_color='blue', fill_opacity=0.2,
                tooltip=f"Radio: {int(d['radius'])}m"
            ).add_to(m)

        # HERRAMIENTA DE DIBUJO (CAPA DE ACCIÓN)
        Draw(
            export=False,
            position='topleft',
            draw_options={
                'circle': {'showRadius': True, 'metric': True, 'shapeOptions': {'color': '#0000FF'}},
                'polyline': False, 'rectangle': False, 'polygon': False, 'marker': False, 'circlemarker': False
            },
            edit_options={'edit': True, 'remove': True}
        ).add_to(m)

        # LEYENDA FIJA DE RADIOS (TABLA INTERNA)
        if st.session_state.dibujos_persistentes:
            filas = "".join([f"<tr><td>{i+1}</td><td>{d['radius']:.0f}m</td></tr>" 
                            for i, d in enumerate(st.session_state.dibujos_persistentes)])
            legend_html = f"""
            <div style="position: fixed; top: 10px; right: 50px; z-index: 10000; background: white; padding: 10px; 
                        border: 2px solid #0000FF; border-radius: 8px; font-family: sans-serif; font-size: 11px;">
                <b style="color: blue;">🔵 Radios Manuales</b>
                <table style="width:100%; border-collapse: collapse; margin-top:5px;">
                    <tr style="background:#eee;"><th>ID</th><th>Radio</th></tr>
                    {filas}
                </table>
            </div>
            """
            m.get_root().html.add_child(folium.Element(legend_html))

        # Renderizado
        map_output = st_folium(m, width="100%", height=750, key="mapa_v4")

    # 3. LÓGICA DE CAPTURA AL PULSAR "SAVE" O TERMINAR DIBUJO
    if map_output and map_output.get("all_drawings"):
        nuevos = []
        for d in map_output["all_drawings"]:
            if 'radius' in d['properties']:
                lng, lat = d['geometry']['coordinates']
                nuevos.append({'lat': lat, 'lon': lng, 'radius': d['properties']['radius']})
        
        if nuevos != st.session_state.dibujos_persistentes:
            st.session_state.dibujos_persistentes = nuevos
            st.rerun()

elif auth_status is False: st.error('Credenciales incorrectas')
