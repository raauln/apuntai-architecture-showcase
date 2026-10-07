# Public architecture sample from routes/api/response.py
# App-specific imports refer to modules intentionally excluded from this showcase.

"""
Helpers de respuesta API para contratos v1.
"""
from flask import g, jsonify

from services.i18n_service import translate_error


def unwrap_legacy_result(result):
    """
    Normaliza resultados legacy de Flask:
    - Response
    - (Response, status)
    - (Response, status, headers)
    - dict
    Devuelve: (payload_dict, status_code)
    """
    status_code = None
    response = result

    if isinstance(result, tuple) and result:
        response = result[0]
        if len(result) > 1 and isinstance(result[1], int):
            status_code = result[1]

    if hasattr(response, 'get_json'):
        payload = response.get_json(silent=True)
        if payload is None:
            text_payload = ''
            if hasattr(response, 'get_data'):
                text_payload = response.get_data(as_text=True) or ''
            payload = {'raw': text_payload}

        if status_code is None and hasattr(response, 'status_code'):
            status_code = int(response.status_code)
    elif isinstance(response, dict):
        payload = response
    else:
        payload = {'value': response}

    if not isinstance(payload, dict):
        payload = {'value': payload}

    if status_code is None:
        status_code = 200

    return payload, status_code


def current_request_id():
    """Obtiene el request_id actual si existe."""
    return getattr(g, 'request_id', None)


def api_ok(data=None, status=200):
    """Respuesta exitosa unificada."""
    payload = {'ok': True}
    if data is not None:
        payload['data'] = data

    request_id = current_request_id()
    if request_id:
        payload['request_id'] = request_id

    return jsonify(payload), status


def api_error(code, message, status=400, details=None):
    """Respuesta de error unificada."""
    localized_message = translate_error(code, fallback_message=message)

    payload = {
        'ok': False,
        'error': {
            'code': code,
            'message': localized_message,
            'details': details,
        }
    }

    request_id = current_request_id()
    if request_id:
        payload['request_id'] = request_id

    return jsonify(payload), status


def normalize_legacy_result(
    result,
    *,
    default_error_code='LEGACY_ERROR',
    default_error_message='No se pudo completar la solicitud.',
    default_error_status=400,
):
    """Convierte respuestas legacy a contrato v1 unificado."""
    payload, status_code = unwrap_legacy_result(result)

    has_error = bool(payload.get('error')) or status_code >= 400
    if has_error:
        message = payload.get('error') or payload.get('message') or default_error_message
        details = dict(payload)
        details.pop('error', None)
        details.pop('message', None)
        details.pop('ok', None)
        details.pop('request_id', None)

        error_code = details.pop('error_code', None) or default_error_code
        normalized_status = status_code if status_code >= 400 else default_error_status

        return api_error(
            error_code,
            str(message),
            status=normalized_status,
            details=details or None,
        )

    return api_ok(payload, status=status_code)
