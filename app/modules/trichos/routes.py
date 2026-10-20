from flask import Blueprint, render_template
from app.modules.auth.routes import login_required, permission_required

trichos_bp = Blueprint('trichos', __name__)

@trichos_bp.route('/')
@login_required
@permission_required('trichos')
def index():
    return render_template('trichos/index.html')
