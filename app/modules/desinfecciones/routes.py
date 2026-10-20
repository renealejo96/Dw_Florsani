from flask import Blueprint, render_template
from app.modules.auth.routes import login_required, permission_required

desinfecciones_bp = Blueprint('desinfecciones', __name__)

@desinfecciones_bp.route('/')
@login_required
@permission_required('desinfecciones')
def index():
    return render_template('desinfecciones/index.html')
