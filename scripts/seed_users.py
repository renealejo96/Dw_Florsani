import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from app import create_app
from app.extensions import db
from app.shared.models import User

def seed_users():
    app = create_app()
    with app.app_context():
        users_data = [
            {
                "username": "admin",
                "password": "admin123",
                "full_name": "Administrador General Florsani",
                "role": "ADMIN",
                "permissions": ["fumigacion", "tpsr", "orden_compra", "bodega", "drench", "trichos", "desinfecciones", "catalogos", "importador"]
            },
            {
                "username": "agronomo",
                "password": "agro123",
                "full_name": "Ing. Agrónomo Jefe",
                "role": "AGRONOMO",
                "permissions": ["fumigacion", "tpsr", "orden_compra", "drench", "trichos", "catalogos"]
            },
            {
                "username": "operario",
                "password": "campo123",
                "full_name": "Técnico de Campo (TPSR)",
                "role": "OPERARIO",
                "permissions": ["tpsr"]
            },
            {
                "username": "bodeguero",
                "password": "bodega123",
                "full_name": "Encargado de Bodega",
                "role": "BODEGA",
                "permissions": ["bodega", "salidas_ver", "salidas_imprimir"]
            }
        ]

        for u in users_data:
            existing = User.query.filter_by(username=u["username"]).first()
            if not existing:
                user = User(
                    username=u["username"],
                    full_name=u["full_name"],
                    role=u["role"]
                )
                user.set_password(u["password"])
                user.permissions = u["permissions"]
                db.session.add(user)
                print(f"[+] Usuario '{u['username']}' ({u['role']}) creado.")
            else:
                print(f"[*] Usuario '{u['username']}' ya existe.")

        db.session.commit()
        print("[OK] Usuarios listos para iniciar sesión.")

if __name__ == "__main__":
    seed_users()
