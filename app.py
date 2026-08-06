import streamlit as st
import pandas as pd
import folium
import geopandas as gpd
import os, io, yaml
import numpy as np
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth
from shapely.geometry import Point
from shapely.ops import unary_union
import streamlit.components.v1 as components

st.set_page_config(page_title="Sistema Pro AMZL - Cobertura Montecarlo", layout="wide")

def normalizar_cp(v):
    try:
        return str(int(float(v))).strip().zfill(5)
    except:
        return str(v).strip().zfill(5)


def obtener_color_rango(v):
    try:
        vol = float(v)
        if vol <= 15:
            return "yellow", "🟡 R1-15"
        elif vol <= 20:
            return "orange", "🟠 R16-20"
        elif vol <= 30:
            return "red", "🔴 R21-30"
        elif vol <= 40:
            return "purple", "🟣 R31-40"
        else:
            return "brown", "🟤 R41+"
    except:
        return "gray", "⚪ Desconocido"

with open('config.yaml') as f:
    config = yaml.load(f, SafeLoader)

auth = stauth.Authenticate(
    config['credentials'],
    config['cookie']['name'],
    config['cookie']['key'],
    config['cookie']['expiry_days']
)

auth.login(location='main')

if st.session_state["authentication_status"]:
    if 'procesado' not in st.session_state:
        st.session_state.procesado = False
    if 'resultados' not in st.session_state:
        st.session_state.resultados = None

    col_m, col_p = st.columns([3, 1.2])

    with col_p:
        st.title("🛡️ Panel AMZL")
        auth.logout('Cerrar Sesión', 'sidebar')
        if not os.path.exists('mapas'):
            st.error("Falta carpeta mapas")
            st.stop()
        archs_geo = sorted([f for f in os.listdir('mapas') if f.endswith('.geojson')])
        estados_disponibles = [f.replace('.geojson', '') for f in archs_geo]
        edo_sel = st.selectbox("📍 Seleccionar Estado:", ["Todos"] + estados_disponibles)
        f_poligonos = st.file_uploader("Archivo Cobertura (ZONA/CP/VOLUMEN)", type=["xlsx"])
        f_zonas = st.file_uploader("Archivo Zonas Círculos (Nombre/Latitud/Longitud/Radio/Volumen)", type=["xlsx"])

        # Parámetro deslizable para controlar la densidad del muestreo estadístico
        n_simulaciones = st.slider("🎯 Puntos de Muestreo Montecarlo:", 2000, 20000, 10000, 2000)

        # 🔘 NUEVO: Control para alternar la visibilidad de las capas de factibilidad en el mapa
        mostrar_factibilidad = st.checkbox("👁️ Mostrar Radios de Factibilidad (5, 10, 15 km)", value=True)
        st.session_state['mostrar_anillos'] = mostrar_factibilidad

        if st.button("🚀 Procesar Información", use_container_width=True, type="primary") and f_poligonos and f_zonas:
            with st.spinner("Ejecutando simulación estadística sobre la cobertura..."):
                df_poly_user = pd.read_excel(f_poligonos)
                df_poly_user.columns = df_poly_user.columns.str.upper().str.strip()
                df_poly_user['CP'] = df_poly_user['CP'].apply(normalizar_cp)
                df_poly_user = df_poly_user.drop_duplicates(subset=['CP'])

                df_zonas_user = pd.read_excel(f_zonas)
                df_zonas_user.columns = df_zonas_user.columns.str.strip()
                mapa_cols = {c: c.upper() for c in df_zonas_user.columns if c.upper() in ['NOMBRE', 'LATITUD', 'LONGITUD', 'RADIO', 'VOLUMEN']}
                df_zonas_user = df_zonas_user.rename(columns=mapa_cols)

                estados_a_cargar = estados_disponibles if edo_sel == "Todos" else [edo_sel]
                gdfs = []
                for e in estados_a_cargar:
                    p = os.path.join("mapas", f"{e}.geojson")
                    if os.path.exists(p):
                        g = gpd.read_file(p)
                        g['ESTADO_PERTENECE'] = e
                        gdfs.append(g)

                gdf_base = pd.concat(gdfs, ignore_index=True)
                cp_col = next((c for c in ['d_codigo', 'd_cp', 'CP', 'CODIGOPOSTAL', 'cp'] if c in gdf_base.columns), gdf_base.columns)
                gdf_base[cp_col] = gdf_base[cp_col].astype(str).apply(normalizar_cp)

                gdf_cobertura = gdf_base.merge(df_poly_user, left_on=cp_col, right_on='CP', how='inner').set_crs("EPSG:4326", allow_override=True)

                if gdf_cobertura.empty:
                    st.warning("⚠️ No se encontraron coincidencias entre los CPs del Excel y los mapas GeoJSON.")
                    st.stop()

                gdf_cobertura = gdf_cobertura.drop_duplicates(subset=['CP'])
                estados_con_cobertura_real = gdf_cobertura['ESTADO_PERTENECE'].unique().tolist()

                for c in ['LATITUD', 'LONGITUD', 'RADIO', 'VOLUMEN']:
                    df_zonas_user[c] = pd.to_numeric(df_zonas_user[c], errors='coerce')
                df_zonas_user = df_zonas_user.dropna(subset=['LATITUD', 'LONGITUD', 'RADIO'])
                pts = [Point(xy) for xy in zip(df_zonas_user['LONGITUD'], df_zonas_user['LATITUD'])]
                gdf_circles = gpd.GeoDataFrame(df_zonas_user, geometry=pts, crs="EPSG:4326")

                gdf_cobertura_m = gdf_cobertura.to_crs("EPSG:6362")
                gdf_circles_m = gdf_circles.to_crs("EPSG:6362")
                gdf_circles_m['geometry'] = gdf_circles_m.apply(lambda r: r['geometry'].buffer(r['RADIO']), axis=1)

                gdf_circles_m['AREA_KM2'] = gdf_circles_m['geometry'].area / 1000000.0
                geom_cir_total = unary_union(gdf_circles_m['geometry'].buffer(0))

                gdf_circles_m_corr = gdf_circles_m.copy()
                for idx, row in gdf_circles_m_corr.iterrows():
                    if row['RADIO'] < 100:
                        base_geom = gdf_circles[gdf_circles['NOMBRE'] == row['NOMBRE']]['geometry'].to_crs("EPSG:6362").iloc[0]
                        gdf_circles_m_corr.at[idx, 'geometry'] = base_geom.buffer(row['RADIO'] * 1000)
                
                reporte_cp_por_zona = []
                reporte_cp_por_estado = []
                anillos_por_estado = {}

                                # =========================================================================
                # 📊 LÓGICA UNIFICADA: CENTROIDE POR ACUMULACIÓN DE PARTNERS DE CADA NODO
                # =========================================================================
                for est in estados_con_cobertura_real:
                    sub_cob = gdf_cobertura_m[gdf_cobertura_m['ESTADO_PERTENECE'] == est]
                    if not sub_cob.empty:
                        g_cob_est = unary_union(sub_cob['geometry'].buffer(0))
                        
                        # Filtramos toda la infraestructura física de partners en este estado
                        zonas_del_estado = gdf_circles_m_corr[gdf_circles_m_corr['geometry'].intersects(g_cob_est)]
                        
                        # Definición unificada de conjuntos para los perímetros y coberturas
                        cps_cubiertos_100 = set()
                        cps_cubiertos_parcial = set()
                        cps_parciales_faltantes_porc = set()
                        cps_perimetro_5km = set()
                        cps_perimetro_5_10km = set()
                        cps_perimetro_gt10km = set()
                        
                        if not sub_cob.empty:
                            # Identificamos los nodos únicos oficiales que vienen en el primer archivo (Columna ZONA)
                            nodos_unicos_maestro = sub_cob['ZONA'].dropna().unique().tolist()
                            centroides_nodos_est = []
                            
                            # Procesamos la infraestructura física agrupada por cada nodo maestro
                            for nodo in nodos_unicos_maestro:
                                # Filtramos la cobertura geográfica específica de este nodo
                                cob_nodo_especifico = sub_cob[sub_cob['ZONA'] == nodo]
                                if cob_nodo_especifico.empty or zonas_del_estado.empty:
                                    continue
                                    
                                g_cob_nodo = unary_union(cob_nodo_especifico['geometry'].buffer(0))
                                
                                # 🎯 ASOCIACIÓN: Encontramos el conjunto de partners/círculos que operan dentro de este nodo
                                partners_del_nodo = zonas_del_estado[zonas_del_estado['geometry'].intersects(g_cob_nodo)]
                                
                                # Si no hay partners pisando la geometría, usamos los círculos más cercanos a ese nodo
                                if partners_del_nodo.empty:
                                    centroide_temp_cob = g_cob_nodo.centroid
                                    distancias_a_partners = zonas_del_estado['geometry'].distance(centroide_temp_cob)
                                    partners_del_nodo = zonas_del_estado.loc[[distancias_a_partners.idxmin()]]
                                
                                # 🎯 EL CENTROIDE ES LA ACUMULACIÓN DEL CONJUNTO DE PARTNERS QUE INTEGRAN EL NODO
                                masa_partners_nodo_m = unary_union(partners_del_nodo['geometry'])
                                centroide_acumulacion_nodo_m = masa_partners_nodo_m.centroid
                                
                                # Almacenamos el punto medio para la evaluación de distancias absolutas de abajo
                                centroides_nodos_est.append(centroide_acumulacion_nodo_m)
                                
                                # Proyectamos a GPS para renderizar el pin en Folium
                                pt_gps = gpd.GeoSeries([centroide_acumulacion_nodo_m], crs="EPSG:6362").to_crs("EPSG:4326").iloc[0]
                                
                                # Generamos los radios concéntricos estables de cobertura perimetral
                                b5 = centroide_acumulacion_nodo_m.buffer(5000)
                                b10 = centroide_acumulacion_nodo_m.buffer(10000)
                                b15 = centroide_acumulacion_nodo_m.buffer(15000)
                                
                                # Guardamos los anillos en la sesión vinculados por Estado y Nodo Maestro
                                clave_mapa = f"{est}_{nodo}"
                                anillos_por_estado[clave_mapa] = {
                                    'centro_lat': pt_gps.y,
                                    'centro_lon': pt_gps.x,
                                    'r5': gpd.GeoSeries([b5], crs="EPSG:6362").to_crs("EPSG:4326").iloc[0].__geo_interface__,
                                    'r10': gpd.GeoSeries([b10], crs="EPSG:6362").to_crs("EPSG:4326").iloc[0].__geo_interface__,
                                    'r15': gpd.GeoSeries([b15], crs="EPSG:6362").to_crs("EPSG:4326").iloc[0].__geo_interface__
                                }
                            
                            # Unión global de la red de partners para calcular intersecciones de área
                            union_total_partners_m = unary_union(zonas_del_estado['geometry']).buffer(0) if not zonas_del_estado.empty else None
                            
                            # Iteramos sobre los CPs del estado para clasificar coberturas y perímetros radiales absolutos
                            for _, cp_row in sub_cob.iterrows():
                                area_real_cp_fija = cp_row['geometry'].area
                                if area_real_cp_fija <= 0:
                                    continue
                                    
                                geom_cp = cp_row['geometry'].buffer(0)
                                centroide_cp = geom_cp.centroid
                                cp_str = cp_row['CP']
                                zona_lbl = cp_row.get('ZONA', 'S/N')
                                
                                # 📦 BLOQUE A: EVALUACIÓN DE COBERTURA GEOMÉTRICA CON ÁREA FIJA
                                if union_total_partners_m is not None and union_total_partners_m.intersects(geom_cp):
                                    try:
                                        area_interseccion = geom_cp.intersection(union_total_partners_m).area
                                        porcentaje_cobertura = (area_interseccion / area_real_cp_fija) * 100
                                    except Exception:
                                        porcentaje_cobertura = 50.0
                                    
                                    porcentaje_cobertura = min(100.0, porcentaje_cobertura)
                                    
                                    if porcentaje_cobertura >= 95:
                                        cps_cubiertos_100.add(f"{zona_lbl}: {cp_str}")
                                    else:
                                        cps_cubiertos_parcial.add(f"{zona_lbl}: {cp_str} ({round(porcentaje_cobertura, 0)}%)")
                                        porcentaje_faltante = 100 - porcentaje_cobertura
                                        cps_parciales_faltantes_porc.add(f"{zona_lbl}: {cp_str} ({round(porcentaje_faltante, 0)}%)")
                                
                                # 🎯 BLOQUE B: CLASIFICACIÓN RADIAL ABSOLUTA DESDE EL CENTRO DE GRAVEDAD DEL NODO
                                if centroides_nodos_est:
                                    distancia_al_centroide = min([centroide.distance(centroide_cp) for centroide in centroides_nodos_est])
                                    
                                    if distancia_al_centroide <= 5000:
                                        cps_perimetro_5km.add(f"{zona_lbl}: {cp_str}")
                                    elif distancia_al_centroide <= 10000:
                                        cps_perimetro_5_10km.add(f"{zona_lbl}: {cp_str}")
                                    else:
                                        cps_perimetro_gt10km.add(f"{zona_lbl}: {cp_str}")
                                else:
                                    cps_perimetro_gt10km.add(f"{zona_lbl}: {cp_str}")
                                            
                        else:
                            for _, cp_row in sub_cob.iterrows():
                                cps_perimetro_gt10km.add(f"{cp_row.get('ZONA', 'S/N')}: {cp_row['CP']}")
                        
                        # Consolidamos los remanentes lejanos junto con los porcentajes faltantes de la cobertura parcial
                        lista_final_faltantes = sorted(list(cps_parciales_faltantes_porc)) + sorted(list(cps_perimetro_gt10km))
                        texto_final_faltantes = ", ".join(lista_final_faltantes) if lista_final_faltantes else "Ninguno"

                        # Inyección con nomenclatura de perímetros limpios solicitada
                        reporte_cp_por_estado.append({"Estado": est.upper(), "Estatus": "Cubierto Total (100%)", "CP": ", ".join(sorted(list(cps_cubiertos_100))) if cps_cubiertos_100 else "Ninguno"})
                        reporte_cp_por_estado.append({"Estado": est.upper(), "Estatus": "Cubierto Parcial (~50%)", "CP": ", ".join(sorted(list(cps_cubiertos_parcial))) if cps_cubiertos_parcial else "Ninguno"})
                        reporte_cp_por_estado.append({"Estado": est.upper(), "Estatus": "perimetro <5 km", "CP": ", ".join(sorted(list(cps_perimetro_5km))) if cps_perimetro_5km else "Ninguno"})
                        reporte_cp_por_estado.append({"Estado": est.upper(), "Estatus": "perimetro 5-10km", "CP": ", ".join(sorted(list(cps_perimetro_5_10km))) if cps_perimetro_5_10km else "Ninguno"})
                        reporte_cp_por_estado.append({"Estado": est.upper(), "Estatus": "perimetro >10km", "CP": texto_final_faltantes})

                        for _, zona_row in gdf_circles_m_corr.iterrows():
                            if zona_row['geometry'].intersects(g_cob_est):
                                cps_actuales_zona = []
                                for _, cp_row in sub_cob.iterrows():
                                    if zona_row['geometry'].intersects(cp_row['geometry']) or zona_row['geometry'].contains(cp_row['geometry'].centroid):
                                        cps_actuales_zona.append(cp_row['CP'])

                                reporte_cp_por_zona.append({
                                    "Zona": zona_row['NOMBRE'],
                                    "Estado": est,
                                    "CPs Cubiertos": ", ".join(sorted(list(set(cps_actuales_zona)))) if cps_actuales_zona else "Ninguno"
                                })

                df_cp_por_estado = pd.DataFrame(reporte_cp_por_estado)
                df_cp_por_zona = pd.DataFrame(reporte_cp_por_zona)
                if not df_cp_por_estado.empty:
                    df_cp_por_estado = df_cp_por_estado[["Estado", "Estatus", "CP"]]
                # =========================================================================

                desglose_estados = []
                for est in estados_con_cobertura_real:
                    sub_cob = gdf_cobertura_m[gdf_cobertura_m['ESTADO_PERTENECE'] == est]
                    if not sub_cob.empty:
                        g_cob_est = unary_union(sub_cob['geometry'].buffer(0))

                        minx, miny, maxx, maxy = g_cob_est.bounds
                        area_caja_cobertura = (maxx - minx) * (maxy - miny)

                        x_rand = np.random.uniform(minx, maxx, n_simulaciones)
                        y_rand = np.random.uniform(miny, maxy, n_simulaciones)
                        puntos_simulados = [Point(x, y) for x, y in zip(x_rand, y_rand)]

                        puntos_en_cobertura = sum(1 for p in puntos_simulados if g_cob_est.contains(p))
                        puntos_en_ocupacion = sum(1 for p in puntos_simulados if g_cob_est.contains(p) and geom_cir_total.contains(p))

                        cob_km2 = (puntos_en_cobertura / n_simulaciones) * (area_caja_cobertura / 1000000.0)
                        ocu_km2 = (puntos_en_ocupacion / n_simulaciones) * (area_caja_cobertura / 1000000.0)
                        lib_km2 = max(0.0, cob_km2 - ocu_km2)

                        if ocu_km2 > 0:
                            eficiencia = (ocu_km2 / cob_km2 * 100) if cob_km2 > 0 else 0.0
                            desglose_estados.append({
                                "Estado": est,
                                "Territorio Cobertura Total (km²)": round(cob_km2, 4),
                                "Territorio Ocupado Total (km²)": round(ocu_km2, 4),
                                "Territorio Libre Total (km²)": round(lib_km2, 4),
                                "Eficiencia de Ocupación": f"{round(eficiencia, 2)}%"
                            })

                df_desglose = pd.DataFrame(desglose_estados)
                if df_desglose.empty:
                   df_desglose = pd.DataFrame(columns=["Estado", "Territorio Cobertura Total (km²)", "Territorio Ocupado Total (km²)", "Territorio Libre Total (km²)", "Eficiencia de Ocupación"])

                estados_validos = df_desglose['Estado'].unique().tolist()
                gdf_cobertura_filtrada = gdf_cobertura[gdf_cobertura['ESTADO_PERTENECE'].isin(estados_validos)]

                st.session_state.resultados = {
                    'estado_nombre': edo_sel,
                    'df_desglose': df_desglose,
                    'gdf_cobertura_wgs84': gdf_cobertura_filtrada.to_crs("EPSG:4326"),
                    # 🎯 SOLUCIÓN: Guardamos la tabla completa con sus columnas de LATITUD y LONGITUD originales
                    'gdf_circles_wgs84': gdf_circles_m.to_crs("EPSG:4326").assign(LATITUD=gdf_circles['LATITUD'], LONGITUD=gdf_circles['LONGITUD']),
                    'df_zonas_detalles': gdf_circles_m[['NOMBRE', 'RADIO', 'VOLUMEN', 'AREA_KM2']].copy(),
                    'df_cp_por_estado': df_cp_por_estado,
                    'df_cp_por_zona': df_cp_por_zona,
                    'anillos_por_estado': anillos_por_estado
                }
                st.session_state.procesado = True

    with col_m:
        if st.session_state.procesado and st.session_state.resultados is not None:
            res = st.session_state.resultados
            
            if not res['gdf_circles_wgs84'].empty:
                c_lat = res['gdf_circles_wgs84']['LATITUD'].mean()
                c_lon = res['gdf_circles_wgs84']['LONGITUD'].mean()
            else:
                c_lat = 23.6345
                c_lon = -102.5528
                
            m = folium.Map(location=[c_lat, c_lon], zoom_start=6 if res['estado_nombre'] == "Todos" else 10, tiles="CartoDB Voyager")
            
            if st.session_state.get('mostrar_anillos', True) and 'anillos_por_estado' in res:
                for est_key, anillos in res['anillos_por_estado'].items():
                    folium.Marker(
                        location=[anillos['centro_lat'], anillos['centro_lon']],
                        icon=folium.Icon(color='purple', icon='crosshairs', prefix='fa'),
                        tooltip=f"Centroide Acumulación: {est_key}"
                    ).add_to(m)

                    # Anillo 1: 5 KM - Verde Punteado
                    folium.GeoJson(
                        anillos['r5'],
                        style_function=lambda x: {'fillColor': 'transparent', 'color': '#2ecc71', 'weight': 2, 'dashArray': '5, 5', 'pointerEvents': 'none'},
                        tooltip="Anillo 5 km (factibilidad inmediata)"
                    ).add_to(m)
                    # Anillo 2: 10 KM - Amarillo Punteado
                    folium.GeoJson(
                        anillos['r10'],
                        style_function=lambda x: {'fillColor': 'transparent', 'color': '#f1c40f', 'weight': 2, 'dashArray': '5, 5', 'pointerEvents': 'none'},
                        tooltip="Anillo 10 km (factibilidad moderada)"
                    ).add_to(m)
                    # Anillo 3: 15 KM - Rojo Punteado
                    folium.GeoJson(
                        anillos['r15'],
                        style_function=lambda x: {'fillColor': 'transparent', 'color': '#e74c3c', 'weight': 2, 'dashArray': '5, 5', 'pointerEvents': 'none'},
                        tooltip="Anillo 15 km (factibilidad lejana)"
                    ).add_to(m)

            # 2. CAPA INTERMEDIA: Polígonos de los Códigos Postales
            for _, r in res['gdf_cobertura_wgs84'].iterrows():
                tt = f"<b>Estado: {r.get('ESTADO_PERTENECE','S/N')}</b><br>ZONA: {r.get('ZONA','S/N')}<br>CP: {r['CP']}<br>Volumen: {r.get('VOLUMEN', 0)}"
                folium.GeoJson(r['geometry'], style_function=lambda x: {'fillColor': '#3186cc', 'color': '#1d4f78', 'weight': 1.5, 'fillOpacity': 0.35}, tooltip=tt).add_to(m)

            # 3. CAPA SUPERIOR: Círculos operativos actuales con Tooltip Combinado Inteligente
            for _, r in res['gdf_circles_wgs84'].iterrows():
                color_hex, r_txt = obtener_color_rango(r['VOLUMEN'])

                geom_circulo = r['geometry']
                cps_bajo_circulo = []
                for _, cp_row in res['gdf_cobertura_wgs84'].iterrows():
                    if geom_circulo.intersects(cp_row['geometry']):
                        cps_bajo_circulo.append(cp_row['CP'])

                txt_cps_atrapados = ", ".join(sorted(list(set(cps_bajo_circulo)))) if cps_bajo_circulo else "Ninguno"

                tt_c = (
                    f"<b>Zona Operativa: {r['NOMBRE']}</b><br>"
                    f"Rango: {r_txt}<br>"
                    f"Volumen: {r['VOLUMEN']}<br>"
                    f"Radio Ope: {r['RADIO']}m<br>"
                    f"-------------------------<br>"
                    f"<b>CPs Ocupados Abajo:</b> {txt_cps_atrapados}"
                )

                folium.GeoJson(
                    r['geometry'],
                    style_function=lambda x, col=color_hex: {'fillColor': col, 'color': 'black', 'weight': 1, 'fillOpacity': 0.55},
                    tooltip=tt_c
                ).add_to(m)

            m_html = m._repr_html_()
            components.html(m_html, height=600)

            st.write("---")
            st.markdown("### 🖥️ Consola de Control de Territorios (Muestreo de Cobertura Montecarlo)")
            st.markdown(f"**Filtro de Consulta Activo:** `{res['estado_nombre']}`")
            st.dataframe(res['df_desglose'], use_container_width=True, hide_index=True)

            st.markdown("### 📍 Cobertura y Proximidad de Códigos Postales por Estado")
            st.dataframe(res['df_cp_por_estado'], use_container_width=True, hide_index=True)

            st.markdown("### ⭕ Cobertura Detallada por cada Zona")
            st.dataframe(res['df_cp_por_zona'], use_container_width=True, hide_index=True)
            st.write("---")

            c1, c2 = st.columns(2)
            with c1:
                st.download_button(label="💾 Descargar Mapa HTML", data=m_html, file_name=f"Mapa_{res['estado_nombre']}.html", mime="text/html", use_container_width=True)
            with c2:
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
                    res['df_desglose'].to_excel(writer, index=False, sheet_name='Resumen por Estado')
                    res['df_zonas_detalles'].rename(columns={'NOMBRE': 'Nombre de la Zona', 'RADIO': 'Radio (m)', 'VOLUMEN': 'Volumen Registrado', 'AREA_KM2': 'Territorio Ocupado Individual (km²)'}).to_excel(writer, index=False, sheet_name='Zonas Detalles')
                    res['df_cp_por_estado'].to_excel(writer, index=False, sheet_name='CPs por Estado')
                    res['df_cp_por_zona'].to_excel(writer, index=False, sheet_name='CPs por Zona')

                st.download_button(label="📊 Descargar Reporte Excel", data=buf.getvalue(), file_name=f"Reporte_{res['estado_nombre']}.xlsx", mime="application/vnd.ms-excel", use_container_width=True)


elif st.session_state["authentication_status"] is False:
    st.error("Error de acceso: Usuario o contraseña incorrectos.")
