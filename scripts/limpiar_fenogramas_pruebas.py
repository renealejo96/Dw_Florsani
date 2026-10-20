#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Script para vaciar las calificaciones y semanas de evaluación de prueba en Fenogramas,
dejando la base de datos limpia para que el usuario genere oficialmente la Semana 43.
Preserva intactos:
  - matriz_fenogramas (catálogo y reglas de labores)
  - plano_cultivo / crop_state_records (censo y estado de cultivo)
  - registro_cosecha / estimados (cosechas históricas)
"""
import sys
import os
from pathlib import Path

# Agregar raíz del proyecto al sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, '/app')

from app import create_app
from app.extensions import db
from sqlalchemy import text

def limpiar_fenogramas():
    flag_file = Path("/app/.fenogramas_limpiados")
    if flag_file.exists():
        print("[INFO] Fenogramas ya fue limpiado previamente. Omitiendo vaciado para proteger datos reales.")
        return

    app = create_app()
    with app.app_context():
        # Salvaguarda: si ya existe la semana 43 o superior, no limpiar
        has_real_data = db.session.execute(text("SELECT 1 FROM semanas_evaluacion WHERE semana >= 43 LIMIT 1;")).scalar()
        if has_real_data:
            print("[INFO] La Semana 43 o superior ya existe en Fenogramas. Omitiendo vaciado para no borrar calificaciones operativas.")
            return

        print("==========================================================")
        print("[FENOGRAMAS] Limpiando calificaciones de prueba...")
        print("==========================================================")
        
        # 1. Truncar calificaciones
        db.session.execute(text("TRUNCATE TABLE calificaciones RESTART IDENTITY CASCADE;"))
        print("[OK] Tabla 'calificaciones' vaciada (0 registros).")
        
        # 2. Truncar semanas de evaluación
        db.session.execute(text("TRUNCATE TABLE semanas_evaluacion CASCADE;"))
        print("[OK] Tabla 'semanas_evaluacion' vaciada (0 registros).")
        
        db.session.commit()
        
        try:
            flag_file.touch()
        except Exception:
            pass

        cnt_matriz = db.session.execute(text("SELECT COUNT(*) FROM matriz_fenogramas;")).scalar()
        cnt_plano = db.session.execute(text("SELECT COUNT(*) FROM plano_cultivo;")).scalar()
        
        print(f"[VERIFICACION]")
        print(f"   - Matriz Fenogramas: {cnt_matriz} registros (CATALOGO INTACTO)")
        print(f"   - Plano Cultivo: {cnt_plano} registros (CENSO INTACTO)")
        print("[OK] Fenogramas listo para generar la Semana 43 por primera vez.")
        print("==========================================================")

if __name__ == '__main__':
    limpiar_fenogramas()
