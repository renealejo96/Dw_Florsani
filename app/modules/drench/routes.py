from flask import Blueprint, render_template
from app.modules.auth.routes import login_required, permission_required

drench_bp = Blueprint('drench', __name__)

@drench_bp.route('/')
@login_required
@permission_required('drench')
def index():
    return render_template('drench/index.html')
