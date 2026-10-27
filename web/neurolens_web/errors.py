from flask import jsonify


def error(code: str, message: str, status: int, **extra):
    """Uniform API error body: {"error": {"code", "message", ...}}."""
    return jsonify(error={"code": code, "message": message, **extra}), status
