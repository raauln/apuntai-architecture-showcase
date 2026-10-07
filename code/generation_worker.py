# Public architecture sample from services/generation_worker_service.py
# App-specific imports refer to modules intentionally excluded from this showcase.

"""
Servicio de worker para jobs de generación en cola DB.
"""
from datetime import datetime, timedelta

from database import db, GenerationJob
from routes.api.generation import _run_generation_task, GENERATION_TASK_TTL_SECONDS
from routes.api.pdf import _run_pdf_task
from routes.api.presentations import _run_presentation_task
from routes.api.exam_simulators import _run_exam_correction_task, _run_exam_generation_task


DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_DELAY_SECONDS = 2.0
DEFAULT_TIMEOUT_SECONDS = 600
DEFAULT_MAX_RUNNING_JOBS = 2
DEFAULT_MAX_RUNNING_PER_TYPE = 1


def _refresh_expiry():
    return datetime.utcnow() + timedelta(seconds=GENERATION_TASK_TTL_SECONDS)


def _coerce_int(value, default, minimum=0):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def _coerce_float(value, default, minimum=0.0):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def _resolve_limits(app):
    return {
        'max_attempts': _coerce_int(
            app.config.get('GENERATION_JOB_MAX_ATTEMPTS', DEFAULT_MAX_ATTEMPTS),
            DEFAULT_MAX_ATTEMPTS,
            minimum=1,
        ),
        'retry_delay_seconds': _coerce_float(
            app.config.get('GENERATION_JOB_RETRY_DELAY_SECONDS', DEFAULT_RETRY_DELAY_SECONDS),
            DEFAULT_RETRY_DELAY_SECONDS,
            minimum=0.0,
        ),
        'max_running_jobs': _coerce_int(
            app.config.get('GENERATION_WORKER_MAX_RUNNING_JOBS', DEFAULT_MAX_RUNNING_JOBS),
            DEFAULT_MAX_RUNNING_JOBS,
            minimum=1,
        ),
        'max_running_notes': _coerce_int(
            app.config.get('GENERATION_WORKER_MAX_RUNNING_NOTES', DEFAULT_MAX_RUNNING_PER_TYPE),
            DEFAULT_MAX_RUNNING_PER_TYPE,
            minimum=1,
        ),
        'max_running_pdf': _coerce_int(
            app.config.get('GENERATION_WORKER_MAX_RUNNING_PDF', DEFAULT_MAX_RUNNING_PER_TYPE),
            DEFAULT_MAX_RUNNING_PER_TYPE,
            minimum=1,
        ),
        'max_running_presentation': _coerce_int(
            app.config.get('GENERATION_WORKER_MAX_RUNNING_PRESENTATION', DEFAULT_MAX_RUNNING_PER_TYPE),
            DEFAULT_MAX_RUNNING_PER_TYPE,
            minimum=1,
        ),
        'max_running_exam': _coerce_int(
            app.config.get('GENERATION_WORKER_MAX_RUNNING_EXAM', DEFAULT_MAX_RUNNING_PER_TYPE),
            DEFAULT_MAX_RUNNING_PER_TYPE,
            minimum=1,
        ),
        'timeout_notes': _coerce_int(
            app.config.get(
                'GENERATION_JOB_TIMEOUT_NOTES_SECONDS',
                app.config.get('GENERATION_JOB_TIMEOUT_SECONDS', DEFAULT_TIMEOUT_SECONDS),
            ),
            DEFAULT_TIMEOUT_SECONDS,
            minimum=10,
        ),
        'timeout_pdf': _coerce_int(
            app.config.get(
                'GENERATION_JOB_TIMEOUT_PDF_SECONDS',
                app.config.get('GENERATION_JOB_TIMEOUT_SECONDS', DEFAULT_TIMEOUT_SECONDS),
            ),
            DEFAULT_TIMEOUT_SECONDS,
            minimum=10,
        ),
        'timeout_presentation': _coerce_int(
            app.config.get(
                'GENERATION_JOB_TIMEOUT_PRESENTATION_SECONDS',
                app.config.get('GENERATION_JOB_TIMEOUT_SECONDS', DEFAULT_TIMEOUT_SECONDS),
            ),
            DEFAULT_TIMEOUT_SECONDS,
            minimum=10,
        ),
        'timeout_exam': _coerce_int(
            app.config.get(
                'GENERATION_JOB_TIMEOUT_EXAM_SECONDS',
                app.config.get('GENERATION_JOB_TIMEOUT_SECONDS', DEFAULT_TIMEOUT_SECONDS),
            ),
            DEFAULT_TIMEOUT_SECONDS,
            minimum=10,
        ),
    }


def _job_type_from_payload(payload):
    raw = ''
    if isinstance(payload, dict):
        raw = str(payload.get('job_type') or '').strip().lower()

    if raw in ('presentation', 'presentations'):
        return 'presentation'
    if raw in ('pdf',):
        return 'pdf'
    if raw in ('exam_generate', 'exam_correct'):
        return raw
    return 'notes'


def _job_type(job):
    return _job_type_from_payload(job.get_request_payload() or {})


def _timeout_for_job_type(job_type, limits):
    if job_type == 'pdf':
        return limits['timeout_pdf']
    if job_type == 'presentation':
        return limits['timeout_presentation']
    if job_type in ('exam_generate', 'exam_correct'):
        return limits['timeout_exam']
    return limits['timeout_notes']


def _running_counts():
    running = GenerationJob.query.filter_by(status='running').all()
    by_type = {'notes': 0, 'pdf': 0, 'presentation': 0, 'exam_generate': 0, 'exam_correct': 0}
    for job in running:
        by_type[_job_type(job)] = by_type.get(_job_type(job), 0) + 1
    return len(running), by_type


def _max_running_for(job_type, limits):
    if job_type == 'pdf':
        return limits['max_running_pdf']
    if job_type == 'presentation':
        return limits['max_running_presentation']
    if job_type in ('exam_generate', 'exam_correct'):
        return limits['max_running_exam']
    return limits['max_running_notes']


def _can_start_job(job_type, limits):
    running_total, running_by_type = _running_counts()
    if running_total >= limits['max_running_jobs']:
        return False
    return running_by_type.get(job_type, 0) < _max_running_for(job_type, limits)


def _retry_or_finish_with_error(job, limits, reason):
    attempts_used = _coerce_int(job.attempts, 0)
    max_attempts = limits['max_attempts']

    if attempts_used < max_attempts:
        job.status = 'pending'
        job.error_message = f"{reason} Reintentando ({attempts_used}/{max_attempts})."
        job.started_at = None
        job.finished_at = None
        job.expires_at = _refresh_expiry()
    else:
        job.status = 'error'
        job.error_message = f"{reason} Se alcanzó el máximo de {max_attempts} intentos."
        job.finished_at = datetime.utcnow()
        job.expires_at = _refresh_expiry()

    db.session.commit()


def _recover_timed_out_running_jobs(limits):
    now = datetime.utcnow()
    running_jobs = GenerationJob.query.filter_by(status='running').all()

    for job in running_jobs:
        started_at = job.started_at or job.updated_at or job.created_at
        if started_at is None:
            continue

        job_type = _job_type(job)
        timeout_seconds = _timeout_for_job_type(job_type, limits)

        if now - started_at < timedelta(seconds=timeout_seconds):
            continue

        _retry_or_finish_with_error(
            job,
            limits,
            f"El job excedió el timeout de {timeout_seconds}s.",
        )


def _mark_job_error(job, message):
    job.status = 'error'
    job.error_message = message
    job.finished_at = datetime.utcnow()
    job.expires_at = _refresh_expiry()
    db.session.commit()


def process_pending_generation_jobs(app, max_jobs=5):
    """Procesa hasta max_jobs pendientes. Retorna cantidad procesada."""
    processed = 0

    with app.app_context():
        limits = _resolve_limits(app)
        _recover_timed_out_running_jobs(limits)

        max_jobs = _coerce_int(max_jobs, 5, minimum=1)
        pending_jobs = GenerationJob.query.filter_by(status='pending').order_by(GenerationJob.created_at.asc()).limit(max(max_jobs * 4, 20)).all()

        for job in pending_jobs:
            if processed >= max_jobs:
                break

            payload = job.get_request_payload()
            if not payload:
                _mark_job_error(job, 'Payload vacío o inválido para ejecutar la tarea.')
                processed += 1
                continue

            job_type = _job_type_from_payload(payload)
            attempts_used = _coerce_int(job.attempts, 0)

            if attempts_used >= limits['max_attempts']:
                _mark_job_error(job, f'Se alcanzó el máximo de {limits["max_attempts"]} intentos.')
                processed += 1
                continue

            if attempts_used > 0 and job.updated_at is not None:
                retry_at = job.updated_at + timedelta(seconds=limits['retry_delay_seconds'])
                if retry_at > datetime.utcnow():
                    continue

            if not _can_start_job(job_type, limits):
                continue

            try:
                job.status = 'running'
                job.error_message = None
                job.started_at = datetime.utcnow()
                job.finished_at = None
                job.attempts = attempts_used + 1
                job.expires_at = _refresh_expiry()
                db.session.commit()
            except Exception:
                db.session.rollback()
                continue

            attempt_number = _coerce_int(job.attempts, 1, minimum=1)
            worker_payload = dict(payload)
            worker_payload['worker_attempt'] = attempt_number

            if job_type == 'pdf':
                _run_pdf_task(job.task_id, app, worker_payload)
            elif job_type == 'presentation':
                _run_presentation_task(job.task_id, app, worker_payload)
            elif job_type == 'exam_generate':
                _run_exam_generation_task(job.task_id, app, worker_payload)
            elif job_type == 'exam_correct':
                _run_exam_correction_task(job.task_id, app, worker_payload)
            elif job_type == 'notes':
                _run_generation_task(job.task_id, app, worker_payload)
            else:
                _mark_job_error(job, f'Tipo de job no soportado: {job_type}')
                processed += 1
                continue

            # Si por alguna razón quedó en running, marcar error defensivo.
            try:
                db.session.expire_all()
                refreshed = GenerationJob.query.filter_by(task_id=job.task_id).first()
                if refreshed and refreshed.status == 'running':
                    _retry_or_finish_with_error(refreshed, limits, 'Worker finalizó sin estado terminal.')
                elif refreshed and refreshed.status == 'error':
                    reason = refreshed.error_message or 'Error en ejecución del job.'
                    _retry_or_finish_with_error(refreshed, limits, reason)
            except Exception:
                db.session.rollback()

            processed += 1

    return processed
