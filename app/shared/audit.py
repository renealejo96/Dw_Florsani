import json
from app.extensions import db
from app.shared.models import AuditLog

def record_audit(module: str, action: str, entity_type: str = None, entity_id: str = None, user: str = 'Agrónomo', details: dict = None):
    """
    Helper to record audit trace events.
    """
    try:
        details_str = json.dumps(details, ensure_ascii=False) if isinstance(details, dict) else str(details or '')
        log = AuditLog(
            module=module,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            user=user or 'Agrónomo',
            details=details_str
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        print(f"Error logging audit: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
