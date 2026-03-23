import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.plugins import Draw
from folium.features import DivIcon
import yaml
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth

# 1. CONFIGURACIÓN Y ESTADO PERSISTENTE
st.set_page_config(page_title="AMZL Hub - Localizador Pro", layout="wide")

if 'dibujos_manuales' not in st.session_state:
    st.session_state.dibujos_manuales = []
if 'map_center' not in st.session_state:
    st.session_state.map_center = [19.4326, -99.1332]

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
        config['credentials'], config['cookie']['name'], config['cookie']['key'],
        cookie_expiry_days=config['cookie']['expiry_days']
    )
    authenticator.login(location='main')
except Exception as e:
    st.error(f"Error: {e}"); st.stop()

if st.session_state.get("authentication_status"):
    col_mapa, col_controles = st.columns([3.5, 1.2]) 

    with col_controles:
        st.title("📍 Control")
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
        # Centrar mapa dinámicamente
        if not puntos_excel.empty and 'map_center' not in st.session_state:
            st.session_state.map_center = [puntos_excel['Latitud'].mean(), puntos_excel['Longitud'].mean()]
        
        m = folium.Map(location=st.session_state.map_center, zoom_start=12)

        # 3. CAPA EXCEL (FIJA Y ESTÁTICA)
        if not puntos_excel.empty:
            for _, fila in puntos_excel.iterrows():
                v = fila.get('Volumen', 0)
                rango = 0 if v==0 else 1 if v<=15 else 2 if v<=20 else 3 if v<=30 else 4 if v<=40 else 5
                if rango in activos:
                    folium.Circle(
                        location=[fila['Latitud'], fila['Longitud']],
                        radius=float(fila.get('Radio', 800)),
                        color='black', weight=1, fill=True,
                        fill_color=obtener_color(v), fill_opacity=0.4,
                        interactive=False
                    ).add_to(m)
                    if mostrar_nombres:
                        folium.Marker(
                            [fila['Latitud'], fila['Longitud']], 
                            icon=DivIcon(html=f'<div style="font-size: 8pt; color: black; font-weight: bold; width:100px;">{fila["Nombre"]}</div>')
                        ).add_to(m)

        # 4. HERRAMIENTA DE DIBUJO (AZULES)
        draw = Draw(
            export=False,
            position='topleft',
            draw_options={
                'circle': {'showRadius': True, 'metric': True, 'shapeOptions': {'color': '#0000FF'}},
                'polyline': False, 'rectangle': False, 'polygon': False, 'marker': False, 'circlemarker': False
            },
            edit_options={'edit': True, 'remove': True}
        )
        draw.add_to(m)

        # SCRIPT JS: Muestra radio en tiempo real al editar/estirar
        js_radio = """
        <script>
        document.addEventListener('DOMContentLoaded', function() {
            var map = L.DomUtil.get('map');
            window.map_obj.on('draw:editresize', function(e) {
                var layer = e.layer;
                var radius = Math.round(layer.getRadius());
                layer.bindTooltip("Radio: " + radius + "m", {permanent: true, direction: 'center'}).openTooltip();
            });
        });
        </script>
        """
        m.get_root().html.add_child(folium.Element(js_radio))

        # Renderizar mapa
        map_output = st_folium(m, width=1200, height=800, key="mapa_v_estable")

    # 5. GESTIÓN DE DATOS Y PERSISTENCIA
    if map_output and map_output.get("all_drawings"):
        datos_temp = []
        for i, dibujo in enumerate(map_output["all_drawings"]):
            geom = dibujo.get('geometry')
            props = dibujo.get('properties')
            if geom and geom['type'] == 'Point' and props and 'radius' in props:
                lng, lat = geom['coordinates']
                datos_temp.append({
                    "Nombre": f"Zona_Nueva_{i+1}",
                    "Latitud": round(lat, 6),
                    "Longitud": round(lng, 6),
                    "Radio_m": round(props['radius'], 1)
                })
        
        # Guardar en session_state para que no se borren al filtrar
        st.session_state.dibujos_manuales = datos_temp

    # MOSTRAR TABLA Y DESCARGA SIEMPRE (Incluso si cambias filtros)
    if st.session_state.dibujos_manuales:
        with col_controles:
            st.subheader("💾 Exportar")
            df_exp = pd.DataFrame(st.session_state.dibujos_manuales)
            st.download_button("📥 Descargar CSV", df_exp.to_csv(index=False).encode('utf-8'), "zonas.csv", "text/csv")
            st.dataframe(df_exp, height=200)

elif st.session_state.get("authentication_status") is False:
    st.error('Acceso denegado')
