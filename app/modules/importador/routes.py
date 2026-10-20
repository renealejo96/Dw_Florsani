import os
from pathlib import Path
from werkzeug.utils import secure_filename
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, jsonify
from app.extensions import db
from app.shared.models import ImportBatch, Product, Litraje, CropStateRecord, Crop, AuditLog
from app.shared.excel_parser import ExcelParserService
from app.shared.audit import record_audit
from app.modules.auth.routes import login_required, permission_required

importador_bp = Blueprint('importador', __name__)

ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'xlsm'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@importador_bp.route('/')
@login_required
@permission_required('importador')
def index():
    batches = ImportBatch.query.order_by(ImportBatch.created_at.desc()).all()
    audit_logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(20).all()
    return render_template('importador/index.html', batches=batches, audit_logs=audit_logs)


@importador_bp.route('/upload', methods=['POST'])
@login_required
@permission_required('importador')
def upload():
    file_type = request.form.get('file_type')
    action_type = request.form.get('action_type', 'commit')  # 'preview' or 'commit'
    mode = request.form.get('mode', 'replace')  # 'replace' or 'append'

    if 'file' not in request.files:
        flash("No se seleccionó ningún archivo.", "danger")
        return redirect(url_for('importador.index'))

    file = request.files['file']
    if file.filename == '':
        flash("Nombre de archivo vacío.", "danger")
        return redirect(url_for('importador.index'))

    if not allowed_file(file.filename):
        flash("Formato no soportado. Por favor sube un archivo Excel (.xlsx, .xls, .xlsm).", "danger")
        return redirect(url_for('importador.index'))

    filename = secure_filename(file.filename)
    upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    file.save(upload_path)

    # Process according to file type
    if file_type == 'PRODUCTOS_DOSIS':
        res = ExcelParserService.parse_products_excel(upload_path)
        if not res['success']:
            flash(f"Error al analizar archivo de productos: {res.get('error')}", "danger")
            return redirect(url_for('importador.index'))

        if action_type == 'commit':
            if mode == 'replace':
                # Deactivate or clear existing
                Product.query.update({'is_active': False})
            
            imported_count = 0
            for item in res['data']:
                p = Product.query.filter_by(code=item['code']).first()
                if p:
                    p.commercial_name = item['commercial_name']
                    p.unit = item['unit']
                    p.dose_fumigation = item['dose_fumigation']
                    p.dose_drench = item['dose_drench']
                    p.pest = item['pest']
                    p.active_ingredient = item['active_ingredient']
                    p.toxicological_category = item['toxicological_category']
                    p.is_active = True
                else:
                    p = Product(
                        code=item['code'],
                        commercial_name=item['commercial_name'],
                        unit=item['unit'],
                        dose_fumigation=item['dose_fumigation'],
                        dose_drench=item['dose_drench'],
                        pest=item['pest'],
                        active_ingredient=item['active_ingredient'],
                        toxicological_category=item['toxicological_category'],
                        is_active=True
                    )
                    db.session.add(p)
                imported_count += 1

            batch = ImportBatch(
                file_type=file_type,
                filename=filename,
                imported_rows=imported_count,
                status='SUCCESS',
                notes=f"Modo: {mode}. Total filas procesadas: {imported_count}"
            )
            db.session.add(batch)
            db.session.commit()
            record_audit('IMPORTADOR', 'IMPORT_EXCEL', 'Product', batch.id, details={'file': filename, 'rows': imported_count})
            flash(f"Se importaron exitosamente {imported_count} productos.", "success")
            return redirect(url_for('importador.index'))

        # Preview mode
        return render_template('importador/preview.html', file_type=file_type, filename=filename, res=res, mode=mode)

    elif file_type == 'LITRAJES':
        res = ExcelParserService.parse_litrajes_excel(upload_path)
        if not res['success']:
            flash(f"Error al analizar archivo de litrajes: {res.get('error')}", "danger")
            return redirect(url_for('importador.index'))

        if action_type == 'commit':
            if mode == 'replace':
                Litraje.query.delete()

            imported_count = 0
            for item in res['data']:
                lit = Litraje.query.filter_by(crop_name=item['crop_name'], age=item['age']).first()
                if lit:
                    lit.liters_per_bed = item['liters_per_bed']
                else:
                    lit = Litraje(
                        crop_name=item['crop_name'],
                        age=item['age'],
                        liters_per_bed=item['liters_per_bed']
                    )
                    db.session.add(lit)
                imported_count += 1

            batch = ImportBatch(
                file_type=file_type,
                filename=filename,
                imported_rows=imported_count,
                status='SUCCESS',
                notes=f"Modo: {mode}. Total reglas de litraje: {imported_count}"
            )
            db.session.add(batch)
            db.session.commit()
            record_audit('IMPORTADOR', 'IMPORT_EXCEL', 'Litraje', batch.id, details={'file': filename, 'rows': imported_count})
            flash(f"Se importaron exitosamente {imported_count} reglas de litrajes.", "success")
            return redirect(url_for('importador.index'))

        return render_template('importador/preview.html', file_type=file_type, filename=filename, res=res, mode=mode)

    elif file_type in ('ESTADO_CULTIVO', 'ESTADO_CULTIVO_COMPRA'):
        header_row = int(request.form.get('header_row', 4))
        sheet_name = request.form.get('sheet_name', 'DATOS')
        target_week = request.form.get('target_week', '').strip()
        
        if not target_week:
            flash("Debes especificar la semana para este plano de estado de cultivo.", "danger")
            return redirect(url_for('importador.index'))

        res = ExcelParserService.parse_crop_state_excel(upload_path, header_row=header_row, sheet_name=sheet_name)
        if not res['success']:
            flash(f"Error al analizar Estado de Cultivo: {res.get('error')}", "danger")
            return redirect(url_for('importador.index'))

        if action_type == 'commit':
            # Delete existing records for this specific week to prevent duplicates
            if request.form.get('mode', 'replace') == 'replace':
                CropStateRecord.query.filter_by(week=target_week).delete()

            tipo_desc = "Orden de Compra (15 Días)" if file_type == 'ESTADO_CULTIVO_COMPRA' else "Fumigación Real"
            batch = ImportBatch(
                file_type=file_type,
                filename=filename,
                week=target_week,
                imported_rows=len(res['data']),
                status='SUCCESS',
                notes=f"[{tipo_desc}] Semana: {target_week}. Hoja: {sheet_name}. Camas estándar: {res['summary']['total_standard_beds']}"
            )
            db.session.add(batch)
            db.session.flush()

            # Auto-discover and register any new crops found in the file
            discovered_crops = set()
            records = []
            for r in res['data']:
                rec = CropStateRecord(
                    batch_id=batch.id,
                    week=target_week,
                    block_full=r['block_full'],
                    block_num=r['block_num'],
                    bed_num=r['bed_num'],
                    suffix=r['suffix'],
                    crop_master=r['crop_master'],
                    product_name=r['product_name'],
                    variety=r['variety'],
                    standard_bed=r['standard_bed'],
                    zone=r['zone'],
                    real_age=r['real_age'],
                    status_raw=r['status_raw']
                )
                records.append(rec)
                cm = (r['crop_master'] or '').strip()
                if cm and cm.upper() not in ('VACIO', 'DESCARTE', 'TUMBAR', 'NAN', 'TOTAL', 'TOTAL_GENERAL', ''):
                    discovered_crops.add(cm)

            # Ensure crops exist in Crop catalog
            for c_name in discovered_crops:
                c_name_norm = c_name.strip()
                existing = Crop.query.filter(db.func.upper(Crop.name) == c_name_norm.upper()).first()
                if not existing:
                    for c_in_db in Crop.query.all():
                        if c_name_norm.upper() in [a.upper() for a in c_in_db.aliases]:
                            existing = c_in_db
                            break
                if not existing:
                    formatted_name = c_name_norm.capitalize() if c_name_norm.isupper() else c_name_norm
                    new_crop = Crop(
                        name=formatted_name,
                        veg_min_age=0,
                        veg_max_age=9,
                        prod_min_age=10,
                        prod_max_age=999,
                        is_active=True,
                        notes=f"Cultivo auto-registrado desde importación ({filename})"
                    )
                    new_crop.aliases = [c_name_norm.upper(), formatted_name]
                    db.session.add(new_crop)

            db.session.bulk_save_objects(records)
            db.session.commit()

            # Sincronizar eventos TPSR (fact_eventos_cultivo) desde el Excel de Estado de Cultivo
            ev_msg = ""
            if file_type == 'ESTADO_CULTIVO':
                try:
                    from scripts.ingestar_semana_excel import ingestar_archivo
                    ev_res = ingestar_archivo(upload_path, target_week=target_week, sync_crop_state=False, engine=db.engine)
                    ev_msg = f" (TPSR sincronizado: +{ev_res.get('nuevos_eventos', 0)} eventos detectados)"
                except Exception as e_ing:
                    print(f"[WARN] Error sincronizando eventos TPSR desde importador: {e_ing}")

            record_audit('IMPORTADOR', 'IMPORT_EXCEL', 'CropStateRecord', batch.id, details={'file': filename, 'week': target_week, 'type': file_type, 'rows': len(records)})
            flash(f"Se importaron exitosamente {len(records)} registros de Estado de Cultivo [{tipo_desc}] para la Semana {target_week}{ev_msg}.", "success")
            return redirect(url_for('importador.index'))

        existing_count = CropStateRecord.query.filter_by(week=target_week).count()
        return render_template(
            'importador/preview.html', 
            file_type=file_type, 
            filename=filename, 
            res=res, 
            mode=mode, 
            target_week=target_week,
            existing_count=existing_count,
            header_row=header_row,
            sheet_name=sheet_name
        )

    flash("Tipo de archivo no reconocido.", "warning")
    return redirect(url_for('importador.index'))


@importador_bp.route('/commit-preview', methods=['POST'])
@login_required
@permission_required('importador')
def commit_preview():
    file_type = request.form.get('file_type')
    filename = request.form.get('filename')
    mode = request.form.get('mode', 'replace')
    target_week = request.form.get('target_week', '').strip()
    header_row = int(request.form.get('header_row', 4))
    sheet_name = request.form.get('sheet_name', 'DATOS')

    upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    if not os.path.exists(upload_path):
        flash("El archivo temporal ha expirado o no existe. Por favor vuelve a seleccionarlo.", "warning")
        return redirect(url_for('importador.index'))

    if file_type == 'PRODUCTOS_DOSIS':
        res = ExcelParserService.parse_products_excel(upload_path)
        if not res['success']:
            flash(f"Error: {res.get('error')}", "danger")
            return redirect(url_for('importador.index'))
        if mode == 'replace':
            Product.query.update({'is_active': False})
        imported_count = 0
        for item in res['data']:
            p = Product.query.filter_by(code=item['code']).first()
            if p:
                p.commercial_name = item['commercial_name']
                p.unit = item['unit']
                p.dose_fumigation = item['dose_fumigation']
                p.dose_drench = item['dose_drench']
                p.pest = item['pest']
                p.active_ingredient = item['active_ingredient']
                p.toxicological_category = item['toxicological_category']
                p.is_active = True
            else:
                p = Product(
                    code=item['code'],
                    commercial_name=item['commercial_name'],
                    unit=item['unit'],
                    dose_fumigation=item['dose_fumigation'],
                    dose_drench=item['dose_drench'],
                    pest=item['pest'],
                    active_ingredient=item['active_ingredient'],
                    toxicological_category=item['toxicological_category'],
                    is_active=True
                )
                db.session.add(p)
            imported_count += 1
        batch = ImportBatch(
            file_type=file_type,
            filename=filename,
            imported_rows=imported_count,
            status='SUCCESS',
            notes=f"Actualización confirmada. Total productos procesados: {imported_count}"
        )
        db.session.add(batch)
        db.session.commit()
        record_audit('IMPORTADOR', 'IMPORT_EXCEL', 'Product', batch.id, details={'file': filename, 'rows': imported_count})
        flash(f"Se sincronizaron exitosamente {imported_count} productos.", "success")
        return redirect(url_for('importador.index'))

    elif file_type == 'LITRAJES':
        res = ExcelParserService.parse_litrajes_excel(upload_path)
        if not res['success']:
            flash(f"Error: {res.get('error')}", "danger")
            return redirect(url_for('importador.index'))
        if mode == 'replace':
            Litraje.query.delete()
        for item in res['data']:
            lit = Litraje(
                crop_name=item['crop_name'],
                age=item['age'],
                liters_per_bed=item['liters_per_bed'],
                is_active=True
            )
            db.session.add(lit)
        batch = ImportBatch(
            file_type=file_type,
            filename=filename,
            imported_rows=len(res['data']),
            status='SUCCESS',
            notes=f"Tabla de litrajes actualizada. Filas: {len(res['data'])}"
        )
        db.session.add(batch)
        db.session.commit()
        record_audit('IMPORTADOR', 'IMPORT_EXCEL', 'Litraje', batch.id, details={'file': filename, 'rows': len(res['data'])})
        flash(f"Se importaron {len(res['data'])} registros de litrajes por cama.", "success")
        return redirect(url_for('importador.index'))

    elif file_type in ('ESTADO_CULTIVO', 'ESTADO_CULTIVO_COMPRA'):
        if not target_week:
            flash("Debes especificar la semana objetivo.", "danger")
            return redirect(url_for('importador.index'))
        res = ExcelParserService.parse_crop_state_excel(upload_path, header_row=header_row, sheet_name=sheet_name)
        if not res['success']:
            flash(f"Error: {res.get('error')}", "danger")
            return redirect(url_for('importador.index'))
        
        # Replace existing records for this week
        previous_count = CropStateRecord.query.filter_by(week=target_week).count()
        CropStateRecord.query.filter_by(week=target_week).delete()

        tipo_desc = "Orden de Compra (15 Días)" if file_type == 'ESTADO_CULTIVO_COMPRA' else "Fumigación Real"
        batch = ImportBatch(
            file_type=file_type,
            filename=filename,
            week=target_week,
            imported_rows=len(res['data']),
            status='SUCCESS',
            notes=f"[{tipo_desc}] Semana: {target_week}. Camas estándar: {res['summary']['total_standard_beds']}"
        )
        db.session.add(batch)
        db.session.flush()

        discovered_crops = set()
        records = []
        for r in res['data']:
            rec = CropStateRecord(
                batch_id=batch.id,
                week=target_week,
                block_full=r['block_full'],
                block_num=r['block_num'],
                bed_num=r['bed_num'],
                suffix=r['suffix'],
                crop_master=r['crop_master'],
                product_name=r['product_name'],
                variety=r['variety'],
                standard_bed=r['standard_bed'],
                zone=r['zone'],
                real_age=r['real_age'],
                status_raw=r['status_raw']
            )
            records.append(rec)
            cm = (r['crop_master'] or '').strip()
            if cm and cm.upper() not in ('VACIO', 'DESCARTE', 'TUMBAR', 'NAN', 'TOTAL', 'TOTAL_GENERAL', ''):
                discovered_crops.add(cm)

        for c_name in discovered_crops:
            c_name_norm = c_name.strip()
            existing = Crop.query.filter(db.func.upper(Crop.name) == c_name_norm.upper()).first()
            if not existing:
                for c_in_db in Crop.query.all():
                    if c_name_norm.upper() in [a.upper() for a in c_in_db.aliases]:
                        existing = c_in_db
                        break
            if not existing:
                formatted_name = c_name_norm.capitalize() if c_name_norm.isupper() else c_name_norm
                new_crop = Crop(
                    name=formatted_name,
                    veg_min_age=0,
                    veg_max_age=9,
                    prod_min_age=10,
                    prod_max_age=999,
                    is_active=True,
                    notes=f"Cultivo auto-registrado ({filename})"
                )
                new_crop.aliases = [c_name_norm.upper(), formatted_name]
                db.session.add(new_crop)

        db.session.bulk_save_objects(records)
        db.session.commit()

        # Sincronizar eventos TPSR (fact_eventos_cultivo) desde el Excel de Estado de Cultivo
        ev_msg = ""
        if file_type == 'ESTADO_CULTIVO':
            try:
                from scripts.ingestar_semana_excel import ingestar_archivo
                ev_res = ingestar_archivo(upload_path, target_week=target_week, sync_crop_state=False, engine=db.engine)
                ev_msg = f" (TPSR sincronizado: +{ev_res.get('nuevos_eventos', 0)} eventos detectados)"
            except Exception as e_ing:
                print(f"[WARN] Error sincronizando eventos TPSR desde importador preview: {e_ing}")

        record_audit('IMPORTADOR', 'IMPORT_EXCEL', 'CropStateRecord', batch.id, details={'file': filename, 'week': target_week, 'type': file_type, 'rows': len(records)})
        if previous_count > 0:
            flash(f"Se reemplazaron {previous_count} camas anteriores y se importaron exitosamente {len(records)} registros de Estado de Cultivo [{tipo_desc}] para la Semana {target_week}{ev_msg}.", "success")
        else:
            flash(f"Se importaron exitosamente {len(records)} registros de Estado de Cultivo [{tipo_desc}] para la Semana {target_week}{ev_msg}.", "success")
        return redirect(url_for('importador.index'))

    return redirect(url_for('importador.index'))


@importador_bp.route('/lotes/<int:batch_id>/eliminar', methods=['POST'])
@login_required
@permission_required('importador')
def eliminar_lote(batch_id):
    batch = ImportBatch.query.get_or_404(batch_id)
    file_type = batch.file_type
    filename = batch.filename
    week = batch.week

    deleted_count = 0
    if file_type in ('ESTADO_CULTIVO', 'ESTADO_CULTIVO_COMPRA'):
        # Delete all crop state records associated with this batch or with this specific week
        records_query = CropStateRecord.query.filter(
            db.or_(
                CropStateRecord.batch_id == batch.id,
                CropStateRecord.week == week
            )
        )
        deleted_count = records_query.count()
        records_query.delete(synchronize_session=False)

    db.session.delete(batch)
    db.session.commit()

    record_audit('IMPORTADOR', 'DELETE_BATCH', file_type, batch_id, details={'file': filename, 'week': week, 'deleted_rows': deleted_count})

    if file_type in ('ESTADO_CULTIVO', 'ESTADO_CULTIVO_COMPRA'):
        flash(f"Se eliminó el lote '{filename}' y sus {deleted_count} registros de camas para la Semana {week}. Ya puedes volver a subir el archivo.", "success")
    else:
        flash(f"Se eliminó el registro de importación del lote '{filename}'.", "success")

    return redirect(url_for('importador.index'))
