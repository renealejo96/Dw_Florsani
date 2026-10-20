"""
==============================================================================
PROYECTO: DATA WAREHOUSE & BACKEND TRANSACCIONAL - GRUPO FLORICULTOR FLORSANI
ARCHIVO: scripts/consultar_estado.py
DESCRIPCIÓN: Script de consulta rápida para verificar el estado de los cultivos
             y el historial de podas directamente desde la terminal.
USO:
    python scripts/consultar_estado.py
    python scripts/consultar_estado.py 38C
    python scripts/consultar_estado.py 11A
==============================================================================
"""

import sys
import psycopg2
import pandas as pd

DB_URL = "postgresql://postgres:admin@localhost:5432/florsani"


def consultar_resumen_global(conn):
    """Muestra el total de registros en las tablas maestras."""
    print("=" * 80)
    print("1. RESUMEN GLOBAL EN POSTGRESQL (Base 'florsani')")
    print("=" * 80)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM dim_bloques;")
        total_blq = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM dim_camas;")
        total_camas = cur.fetchone()[0]
        cur.execute("SELECT tipo_evento, COUNT(*) FROM fact_eventos_cultivo GROUP BY tipo_evento ORDER BY COUNT(*) DESC;")
        eventos = cur.fetchall()

    print(f"  • Bloques físicos en dim_bloques : {total_blq:>6}")
    print(f"  • Camas físicas en dim_camas     : {total_camas:>6}")
    print("  • Eventos en fact_eventos_cultivo:")
    for tipo, cnt in eventos:
        print(f"      - {tipo:<10}: {cnt:>6} eventos")


def consultar_bloque(conn, codigo_bloque_busqueda):
    """Muestra la información física y el historial de podas de un bloque."""
    print("\n" + "=" * 80)
    print(f"2. INFORMACIÓN FÍSICA DEL BLOQUE: {codigo_bloque_busqueda}")
    print("=" * 80)
    query_blq = f"""
    SELECT 
        codigo_bloque, zona, tecnico_de_riego, total_camas_fisicas, 
        total_camas_estandar, area_neta_m2, area_bruta_m2
    FROM dim_bloques
    WHERE codigo_bloque LIKE '{codigo_bloque_busqueda}%'
    ORDER BY codigo_bloque;
    """
    with conn.cursor() as cur:
        cur.execute(query_blq)
        rows = cur.fetchall()
        if not rows:
            print(f"  [!] No se encontró ningún bloque con el código '{codigo_bloque_busqueda}'.")
            return
        
        print(f"{'Bloque':<8} | {'Zona':<12} | {'Técnico':<8} | {'Camas Fís':<10} | {'Camas Est':<10} | {'m2 Netos':<10} | {'m2 Brutos'}")
        print("-" * 80)
        for b in rows:
            print(f"{b[0]:<8} | {str(b[1]):<12} | {str(b[2]):<8} | {b[3]:<10} | {float(b[4]):<10.2f} | {float(b[5]):<10.2f} | {float(b[6]):.2f}")

    print("\n" + "=" * 80)
    print(f"3. HISTORIAL DE EVENTOS Y PODAS: {codigo_bloque_busqueda} (Cama 1)")
    print("=" * 80)
    query_eventos = f"""
    SELECT 
        id_cama, codigo_bloque, tipo_evento, num_poda, fecha_evento, 
        semana_campo, producto_maestro, variedad, origen_evento
    FROM fact_eventos_cultivo
    WHERE codigo_bloque LIKE '{codigo_bloque_busqueda}%' AND (id_cama LIKE '%-C1' OR id_cama LIKE '%-C1_%')
    ORDER BY id_cama, fecha_evento ASC;
    """
    with conn.cursor() as cur:
        cur.execute(query_eventos)
        ev_rows = cur.fetchall()
        if not ev_rows:
            print("  [!] No se encontraron eventos para la cama 1 de este bloque.")
            return

        print(f"{'Cama':<12} | {'Bloque':<8} | {'Evento':<8} | {'Poda':<5} | {'Fecha Evento':<12} | {'Sem':<5} | {'Producto':<12} | {'Variedad':<20}")
        print("-" * 95)
        for r in ev_rows:
            poda_str = str(r[3]) if r[3] is not None else "-"
            print(f"{r[0]:<12} | {r[1]:<8} | {r[2]:<8} | {poda_str:<5} | {str(r[4]):<12} | {str(r[5]):<5} | {str(r[6]):<12} | {str(r[7]):<20}")


def main():
    bloque_a_consultar = sys.argv[1].upper() if len(sys.argv) > 1 else "38C"
    conn = psycopg2.connect(DB_URL)
    consultar_resumen_global(conn)
    consultar_bloque(conn, bloque_a_consultar)
    conn.close()


if __name__ == "__main__":
    main()
