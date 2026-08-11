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

                @st.cache_data
                def generar_mapa_base_cached(edo_sel, estados_disponibles, df_poly_user):
                    estados_a_cargar = estados_disponibles if edo_sel == "Todos" else [edo_sel]
                    gdfs = []
                    for e in estados_a_cargar:
                        p = os.path.join("mapas", f"{e}.geojson")
                        if os.path.exists(p):
                            g = gpd.read_file(p)
                            g['ESTADO_PERTENECE'] = e
                            gdfs.append(g)
            
                    if not gdfs:
                        return gpd.GeoDataFrame()
        
                    gdf_base = pd.concat(gdfs, ignore_index=True)
                    cp_col = next((c for c in ['d_codigo', 'd_cp', 'CP', 'CODIGOPOSTAL', 'cp'] if c in gdf_base.columns), gdf_base.columns[0])
                    gdf_base[cp_col] = gdf_base[cp_col].astype(str).apply(normalizar_cp)
    
                    gdf_cob = gdf_base.merge(df_poly_user, left_on=cp_col, right_on='CP', how='inner').set_crs("EPSG:4326", allow_override=True)
                    return gdf_cob

                # 🚀 EJECUCIÓN INMEDIATA: Llamamos a la función con memoria persistente
                gdf_cobertura = generar_mapa_base_cached(edo_sel, estados_disponibles, df_poly_user)

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

             # 🎯 EXTRACCIÓN DEL MES: Tomamos el nombre base del archivo subido (ej: "JUNIO.xlsx" -> "JUNIO")
                import os
                nombre_archivo_zonas = f_zonas.name if hasattr(f_zonas, 'name') else "JUNIO.xlsx"
                mes_extraido = os.path.splitext(nombre_archivo_zonas)[0].upper()
                gdf_circles_m['Territorio MES'] = mes_extraido
                
                # 🎯 IDENTIFICACIÓN DEL ESTADO: Cruzamos espacialmente cada círculo contra los estados cartográficos
                if 'geometry' in gdf_circles_m.columns and not gdf_cobertura.empty:
                    circles_gps = gdf_circles_m.to_crs(gdf_cobertura.crs)
                    joined = gpd.sjoin(circles_gps, gdf_cobertura[['geometry', 'ESTADO_PERTENECE']], how='left', predicate='intersects')
                    gdf_circles_m['ESTADO'] = joined.groupby(joined.index)['ESTADO_PERTENECE'].first().fillna('DESCONOCIDO').str.upper()
                else:
                    gdf_circles_m['ESTADO'] = 'DESCONOCIDO'

                gdf_circles_m_corr = gdf_circles_m.copy()
                for idx, row in gdf_circles_m_corr.iterrows():
                    if row['RADIO'] < 100:
                        base_geom = gdf_circles[gdf_circles['NOMBRE'] == row['NOMBRE']]['geometry'].to_crs("EPSG:6362").iloc[0]
                        gdf_circles_m_corr.at[idx, 'geometry'] = base_geom.buffer(row['RADIO'] * 1000)
                
                reporte_cp_por_zona = []
                reporte_cp_por_estado = []
                anillos_por_estado = {}

                # =========================================================================
                # 📊 LÓGICA LOGÍSTICA METROPOLITANA: CENTROIDE ÚNICO POR NODO (CRUZA ESTADOS)
                # =========================================================================
                # 🎯 PASO 1: Identificamos y precalculamos los centroides basados estrictamente en cada NODO
                nodos_unicos_maestro = gdf_cobertura['ZONA'].dropna().unique().tolist()
                centroides_nodos_globales = []
                
                for nodo in nodos_unicos_maestro:
                    # Filtramos la cobertura completa de este nodo a nivel nacional (cruza fronteras)
                    cob_nodo_completa = gdf_cobertura[gdf_cobertura['ZONA'] == nodo]
                    if cob_nodo_completa.empty or gdf_circles_m_corr.empty:
                        continue
                        
                    g_cob_nodo_global = unary_union(cob_nodo_completa['geometry'].to_crs("EPSG:6362").buffer(0))
                    
                    # ASOCIACIÓN METROPOLITANA: Encontramos TODOS los partners que operan en este nodo (Coahuila + Durango + etc)
                    partners_del_nodo = gdf_circles_m_corr[gdf_circles_m_corr['geometry'].intersects(g_cob_nodo_global)]
                    
                    if partners_del_nodo.empty:
                        centroide_temp_cob = g_cob_nodo_global.centroid
                        distancias_a_partners = gdf_circles_m_corr['geometry'].distance(centroide_temp_cob)
                        partners_del_nodo = gdf_circles_m_corr.loc[[distancias_a_partners.idxmin()]]
                    
                    # CALCULO DE MASA: Un solo centroide para la acumulación total de partners del nodo
                    masa_partners_nodo_m = unary_union(partners_del_nodo['geometry'])
                    centroide_acumulacion_nodo_m = masa_partners_nodo_m.centroid
                    
                    # Guardamos el centroide en la lista global para la evaluación de distancias radiales
                    centroides_nodos_globales.append(centroide_acumulacion_nodo_m)
                    
                    # Proyectamos a GPS para Folium
                    pt_gps = gpd.GeoSeries([centroide_acumulacion_nodo_m], crs="EPSG:6362").to_crs("EPSG:4326").iloc[0]
                    
                    # Generamos los radios de perímetro estables concéntricos
                    b5 = centroide_acumulacion_nodo_m.buffer(5000)
                    b10 = centroide_acumulacion_nodo_m.buffer(10000)
                    b15 = centroide_acumulacion_nodo_m.buffer(15000)
                    
                    # Almacenamos en el diccionario usando como clave únicamente el nombre del Nodo
                    anillos_por_estado[nodo] = {
                        'centro_lat': pt_gps.y,
                        'centro_lon': pt_gps.x,
                        'r5': gpd.GeoSeries([b5], crs="EPSG:6362").to_crs("EPSG:4326").iloc[0].__geo_interface__,
                        'r10': gpd.GeoSeries([b10], crs="EPSG:6362").to_crs("EPSG:4326").iloc[0].__geo_interface__,
                        'r15': gpd.GeoSeries([b15], crs="EPSG:6362").to_crs("EPSG:4326").iloc[0].__geo_interface__
                    }

                # 🎯 PASO 2: Clasificamos y segmentamos los reportes de salida manteniendo la división por Estado
                union_total_partners_m = unary_union(gdf_circles_m_corr['geometry']).buffer(0) if not gdf_circles_m_corr.empty else None

                for est in estados_con_cobertura_real:
                    sub_cob = gdf_cobertura_m[gdf_cobertura_m['ESTADO_PERTENECE'] == est]
                    if not sub_cob.empty:
                        
                        cps_cubiertos_100 = set()
                        cps_cubiertos_parcial = set()
                        cps_parciales_faltantes_porc = set()
                        cps_perimetro_5km = set()
                        cps_perimetro_5_10km = set()
                        cps_perimetro_gt10km = set()
                        
                        for _, cp_row in sub_cob.iterrows():
                            # 🎯 CONGELAR TAMAÑO REAL: Alineado perfectamente con 24 espacios a la izquierda
                            area_real_cp_fija = cp_row['geometry'].area
                            if area_real_cp_fija <= 0:
                                continue
                                
                            geom_cp = cp_row['geometry'].buffer(0)
                            centroide_cp = geom_cp.centroid
                            cp_str = cp_row['CP']
                            zona_lbl = cp_row.get('ZONA', 'S/N')
                            
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
                                
                                # Si el porcentaje es nulo o menor a 0.01 se marca como libre
                                if porcentaje_cobertura < 0.01:
                                    cp_str = f"LIBRE - {cp_str}"
                            else:
                                # Si ni siquiera intersecta la mancha de partners, entra directo como LIBRE
                                cp_str = f"LIBRE - {cp_str}"

                            # 📦 BLOQUE B: CLASIFICACIÓN RADIAL ABSOLUTA RESPECTO AL CENTROIDE GLOBAL DEL NODO
                            if centroides_nodos_globales:
                                distancia_al_centroide = min([centroide.distance(centroide_cp) for centroide in centroides_nodos_globales])
                                
                                if distancia_al_centroide <= 5000:
                                    cps_perimetro_5km.add(f"{zona_lbl}: {cp_str}")
                                elif distancia_al_centroide <= 10000:
                                    cps_perimetro_5_10km.add(f"{zona_lbl}: {cp_str}")
                                else:
                                    cps_perimetro_gt10km.add(f"{zona_lbl}: {cp_str}")
                            else:
                                cps_perimetro_gt10km.add(f"{zona_lbl}: {cp_str}")
                        # 🎯 SEPARACIÓN EN LISTAS DE CONTROL OPERATIVO
                                               # 🎯 CORRECCIÓN: Filtramos basándonos ÚNICAMENTE en los CPs reales del primer archivo
                        cps_reales_primer_archivo = set(sub_cob['CP'].astype(str).tolist())
                        
                        cps_solo_libres = [cp for cp in (list(cps_perimetro_5km) + list(cps_perimetro_5_10km) + list(cps_perimetro_gt10km)) if "LIBRE" in cp]
                        cps_solo_libres_clean = [cp.replace("LIBRE - ", "") for cp in cps_solo_libres]
                        
                        # Guardamos en 'libre' solo si el CP realmente existía en la base del primer archivo
                        cps_libres_filtrados = [cp for cp in cps_solo_libres_clean if cp.split(": ")[-1] in cps_reales_primer_archivo]
                        
                        cps_p5_limpios = [cp for cp in cps_perimetro_5km if "LIBRE" not in cp]
                        cps_p10_limpios = [cp for cp in cps_perimetro_5_10km if "LIBRE" not in cp]
                        cps_p15_limpios = [cp for cp in cps_perimetro_gt10km if "LIBRE" not in cp]
                        
                        # 🚀 INYECCIÓN CON MANDATORIOS DE MAPA (Mayúsculas Iniciales Exactas para revivir el color azul)
                        reporte_cp_por_estado.append({"Estado": est.upper(), "Estatus": "Cubierto Total (100%)", "CP": ", ".join(sorted(list(cps_cubiertos_100))) if cps_cubiertos_100 else "Ninguno"})
                        reporte_cp_por_estado.append({"Estado": est.upper(), "Estatus": "Cubierto Parcial (~50%)", "CP": ", ".join(sorted(list(cps_cubiertos_parcial))) if cps_cubiertos_parcial else "Ninguno"})
                        reporte_cp_por_estado.append({"Estado": est.upper(), "Estatus": "libre", "CP": ", ".join(sorted(cps_libres_filtrados)) if cps_libres_filtrados else "Ninguno"})
                        reporte_cp_por_estado.append({"Estado": est.upper(), "Estatus": "perimetro 5km", "CP": ", ".join(sorted(cps_p5_limpios)) if cps_p5_limpios else "Ninguno"})
                        reporte_cp_por_estado.append({"Estado": est.upper(), "Estatus": "perimetro 5-10km", "CP": ", ".join(sorted(cps_p10_limpios)) if cps_p10_limpios else "Ninguno"})
                        reporte_cp_por_estado.append({"Estado": est.upper(), "Estatus": "perimetro 10-15km", "CP": ", ".join(sorted(cps_p15_limpios)) if cps_p15_limpios else "Ninguno"})

                        for _, zona_row in gdf_circles_m_corr.iterrows():
                            # Verificamos si la zona interactúa con la cobertura utilizando una variable que sí existe en la memoria
                            if union_total_partners_m is not None and zona_row['geometry'].intersects(union_total_partners_m):
                                cps_actuales_zona = []
                                for _, cp_row in sub_cob.iterrows():
                                    geom_cp = cp_row['geometry'].buffer(0)
                                    if zona_row['geometry'].intersects(geom_cp) or zona_row['geometry'].contains(geom_cp.centroid):
                                        cps_actuales_zona.append(cp_row['CP'])
                                
                                if cps_actuales_zona:
                                    reporte_cp_por_zona.append({
                                        "Zona": zona_row['NOMBRE'],
                                        "Estado": est.upper(),
                                        "CPs Cubiertos": ", ".join(sorted(list(set(cps_actuales_zona))))
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

                                               # 🎯 CÁLCULO GEOMÉTRICO DIRECTO Y EXACTO (Reemplaza a Montecarlo)
                        cob_km2 = g_cob_est.area / 1000000.0
                        
                        if union_total_partners_m is not None:
                            union_total_partners_clean = union_total_partners_m.buffer(0)
                            interseccion_ocupada = g_cob_est.intersection(union_total_partners_clean)
                            ocu_km2 = interseccion_ocupada.area / 1000000.0
                        else:
                            ocu_km2 = 0.0
                            
                        lib_km2 = max(0.0, cob_km2 - ocu_km2)
                        
                        if cob_km2 > 0:
                            eficiencia = (ocu_km2 / cob_km2) * 100.0
                        else:
                            eficiencia = 0.0

                # 🎯 CORRECCIÓN: Nombres de columnas idénticos a los de tu interfaz web
                        desglose_estados.append({
                            "Estado": est.upper(),
                            "Territorio Cobertura Total (km²)": round(cob_km2, 2),
                            "Territorio Ocupado Total (km²)": round(ocu_km2, 2),
                            "Territorio Libre Total (km²)": round(lib_km2, 2),
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
                    'df_zonas_detalles': gdf_circles_m[['NOMBRE', 'RADIO', 'VOLUMEN', 'AREA_KM2', 'Territorio MES', 'ESTADO']].copy().rename(columns={
                        'NOMBRE': 'Nombre de la Zona',
                        'RADIO': 'Radio (m)',
                        'AREA_KM2': 'Territorio'
                    }),

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
                for nodo_key, anillos in res['anillos_por_estado'].items():
                    folium.Marker(
                        location=[anillos['centro_lat'], anillos['centro_lon']],
                        icon=folium.Icon(color='purple', icon='crosshairs', prefix='fa'),
                        tooltip=f"Centroide Nodo: {str(nodo_key).upper()}"
                    ).add_to(m)
                    
                    # Anillo 1: <5 km - Verde Punteado
                    folium.GeoJson(
                        anillos['r5'], 
                        style_function=lambda x: {'fillColor': 'transparent', 'color': '#2ecc71', 'weight': 2, 'dashArray': '5, 5', 'pointerEvents': 'none'}, 
                        tooltip=f"perimetro <5 km ({nodo_key})"
                    ).add_to(m)
                    
                    # Anillo 2: 5-10 km - Amarillo Punteado
                    folium.GeoJson(
                        anillos['r10'], 
                        style_function=lambda x: {'fillColor': 'transparent', 'color': '#f1c40f', 'weight': 2, 'dashArray': '5, 5', 'pointerEvents': 'none'}, 
                        tooltip=f"perimetro 5-10km ({nodo_key})"
                    ).add_to(m)
                    
                    # Anillo 3: >10 km - Rojo Punteado
                    folium.GeoJson(
                        anillos['r15'], 
                        style_function=lambda x: {'fillColor': 'transparent', 'color': '#e74c3c', 'weight': 2, 'dashArray': '5, 5', 'pointerEvents': 'none'}, 
                        tooltip=f"perimetro >10km ({nodo_key})"
                    ).add_to(m)

                    # # 2. CAPA INTERMEDIA: Polígonos de los Códigos Postales Elegibles Reales
                    # Filtramos la cartografía base cruzando directamente contra la lista de CPs de tu primer archivo
                if edo_sel == "Todos":
                    cps_primer_archivo = set(df['CP'].astype(str).tolist())
                    gdf_mapa_azul = gdf_partners.copy()
                else:
                    cps_primer_archivo = set(sub_cob['CP'].astype(str).tolist())
                    gdf_mapa_azul = gdf_partners.copy()
              
                    # Convertimos las coordenadas espaciales a formato GPS estándar para que Folium lo lea sin distorsión
                gdf_mapa_azul_wgs84 = gdf_mapa_azul.to_crs("EPSG:4326")
        
                    # Inyectamos la capa de polígonos fijos de un solo golpe (Mil veces más rápido que un ciclo for)
                folium.GeoJson(
                    gdf_mapa_azul_wgs84.to_json(),
                    style_function=lambda x: {
                        'fillColor': '#1e3a8a',  # 🔵 AZUL REY BRILLANTE oficial para tus CPs elegibles del primer archivo
                        'color': '#ffffff',      # Borde blanco de división limpio entre CPs
                        'weight': 1.5,
                        'fillOpacity': 0.4       # Opacidad perfecta para ver las calles y los círculos de fondo
                    },
                    tooltip=folium.GeoJsonTooltip(
                        fields=['CP', 'ESTADO_PERTENECE'],
                        aliases=['Código Postal:', 'Estado:'],
                        localize=True
                    )
                ).add_to(m)
        
            # 3. CAPA SUPERIOR: Círculos operativos actuales con Tooltip Combinado Inteligente
            for _, r in res['gdf_circles_wgs84'].iterrows():
                color_hex, r_text = obtener_color_rango(r['VOLUMEN'])
            
                geom_circulo = r['geometry']
                cps_bajo_circulo = []
            
            # 🚀 SOLUCIÓN GLOBAL: Intersectamos contra la base cartográfica nacional viva sin importar el estado
                for _, cp_row in gdf_cobertura.iterrows():
                    if geom_circulo.intersects(cp_row['geometry']):
                        cps_bajo_circulo.append(str(cp_row['CP']))

                txt_cps_atrapados = ", ".join(sorted(list(set(cps_bajo_circulo)))) if cps_bajo_circulo else "Ninguno"

                tt_c = (
                    f"<b>Zona Operativa: {r['NOMBRE']}</b><br>"
                    f"Rango: {r_text}<br>"
                    f"Volumen: {r['VOLUMEN']}<br>"
                    f"Radio Ope: {r['RADIO']}m<br>"
                    f"-------------------------<br>"
                    f"<b>CPs Ocupados Abajo:</b> {txt_cps_atrapados}"
                )

                folium.GeoJson(
                    geom_circulo,
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
