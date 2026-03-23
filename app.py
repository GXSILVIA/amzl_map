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
    st.error(f"Error de config: {e}"); st.stop()

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
        centro = [puntos_excel['Latitud'].mean(), puntos_excel['Longitud'].mean()] if not puntos_excel.empty else [19.4326, -99.1332]
        m = folium.Map(location=centro, zoom_start=12)

        # 3. CAPA FIJA (EXCEL) - NO EDITABLE
        # Usamos un FeatureGroup específico para que NO sea parte de la capa de dibujo
        fg_fijo = folium.FeatureGroup(name="Capa Base (Fija)").add_to(m)
        
        if not puntos_excel.empty:
            for _, fila in puntos_excel.iterrows():
                v = fila.get('Volumen', 0)
                rango = 0 if v==0 else 1 if v<=15 else 2 if v<=20 else 3 if v<=30 else 4 if v<=40 else 5
                
                if rango in activos:
                    folium.Circle(
                        location=[fila['Latitud'], fila['Longitud']],
                        radius=float(fila.get('Radio', 800)),
                        color='black', weight=1,
                        fill=True, fill_color=obtener_color(v), fill_opacity=0.4,
                        interactive=False # ESTO EVITA QUE SE PUEDAN EDITAR
                    ).add_to(fg_fijo)

                    if mostrar_nombres:
                        folium.Marker(
                            [fila['Latitud'], fila['Longitud']], 
                            icon=DivIcon(html=f'<div style="font-size: 8pt; color: black; font-weight: bold; width:100px;">{fila["Nombre"]}</div>')
                        ).add_to(fg_fijo)

        # 4. HERRAMIENTA DE DIBUJO (SOLO PARA CÍRCULOS AZULES NUEVOS)
        # showRadius=True funciona al crear. Para edición agregamos JS abajo.
        draw = Draw(
            export=False,
            position='topleft',
            draw_options={
                'circle': {'showRadius': True, 'metric': True, 'shapeOptions': {'color': '#0000FF', 'fillOpacity': 0.5}},
                'polyline': False, 'rectangle': False, 'polygon': False, 'marker': False, 'circlemarker': False
            },
            edit_options={'edit': True, 'remove': True}
        )
        draw.add_to(m)

        # JS PARA MOSTRAR RADIO DURANTE EDICIÓN
        js_radio = """
        <script>
        document.addEventListener('DOMContentLoaded', function() {
            var map = L.DomUtil.get('map');
            window.map_obj.on('draw:editresize draw:editmove', function(e) {
                var layer = e.layer;
                if (layer instanceof L.Circle) {
                    var radius = Math.round(layer.getRadius());
                    layer.bindTooltip("Radio: " + radius + "m", {permanent: true, direction: 'center'}).openTooltip();
                }
            });
        });
        </script>
        """
        m.get_root().html.add_child(folium.Element(js_radio))

        # Renderizar mapa
        map_output = st_folium(m, width="100%", height=800, key="mapa_final_estable")

    # 5. EXPORTACIÓN (SOLO DIBUJOS MANUALES NUEVOS)
    if map_output and map_output.get("all_drawings") is not None:
        datos_nuevos = []
        for i, dibujo in enumerate(map_output["all_drawings"]):
            geom = dibujo.get('geometry')
            props = dibujo.get('properties')
            if geom and geom['type'] == 'Point' and props and 'radius' in props:
                lng, lat = geom['coordinates']
                rad = props['radius']
                datos_nuevos.append({
                    "Nombre": f"Zona_Nueva_{i+1}",
                    "Latitud": round(lat, 6),
                    "Longitud": round(lng, 6),
                    "Radio_m": round(rad, 1)
                })

        if datos_nuevos:
            with col_controles:
                st.subheader("💾 Exportar Nuevos")
                df_exp = pd.DataFrame(datos_nuevos)
                st.download_button("📥 Bajar CSV", df_exp.to_csv(index=False).encode('utf-8'), "nuevas_zonas.csv", "text/csv")
                st.dataframe(df_exp, height=150)

elif st.session_state.get("authentication_status") is False:
    st.error('Acceso denegado')
