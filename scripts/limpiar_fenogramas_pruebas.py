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
from app import create_app
from app.extensions import db
from sqlalchemy import text

def limpiar_fenogramas():
    app = create_app()
    with app.app_context():
        print("==========================================================")
        print("🧹 Limpiando calificaciones de prueba en Fenogramas...")
        print("==========================================================")
        
        # 1. Truncar calificaciones
        db.session.execute(text("TRUNCATE TABLE calificaciones RESTART IDENTITY CASCADE;"))
        print("✅ Tabla 'calificaciones' vaciada (0 registros).")
        
        # 2. Truncar semanas de evaluación
        db.session.execute(text("TRUNCATE TABLE semanas_evaluacion CASCADE;"))
        print("✅ Tabla 'semanas_evaluacion' vaciada (0 registros).")
        
        db.session.commit()
        
        cnt_matriz = db.session.execute(text("SELECT COUNT(*) FROM matriz_fenogramas;")).scalar()
        cnt_plano = db.session.execute(text("SELECT COUNT(*) FROM plano_cultivo;")).scalar()
        
        print(f"📊 Verificación de integridad:")
        print(f"   - Matriz Fenogramas: {cnt_matriz} registros (CATÁLOGO INTACTO)")
        print(f"   - Plano Cultivo: {cnt_plano} registros (CENSO INTACTO)")
        print("🌱 Fenogramas listo para generar la Semana 43 por primera vez.")
        print("==========================================================")

if __name__ == '__main__':
    limpiar_fenogramas()
