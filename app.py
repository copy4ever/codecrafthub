from datetime import datetime
import json
import re
from flask_cors import CORS
from pathlib import Path

from flask import Flask, jsonify, request


app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# Store courses.json in the same folder as app.py.
COURSES_FILE = Path(__file__).parent / "courses.json"

# These are the only statuses accepted by the API.
ALLOWED_STATUSES = {
    "Not Started",
    "In Progress",
    "Completed",
}

# This pattern ensures dates use exactly YYYY-MM-DD formatting.
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class StorageError(Exception):
    """Raised when courses.json cannot be read or written."""


def read_courses():
    """
    Read courses from courses.json.

    If the file does not exist, create it with an empty JSON array.
    Invalid JSON and other file errors are reported as storage errors.
    """
    if not COURSES_FILE.exists():
        try:
            COURSES_FILE.write_text("[]\n", encoding="utf-8")
        except OSError as error:
            raise StorageError(
                f"Unable to create courses.json: {error}"
            ) from error

        return []

    try:
        with COURSES_FILE.open("r", encoding="utf-8") as file:
            courses = json.load(file)
    except json.JSONDecodeError as error:
        raise StorageError(
            f"courses.json contains invalid JSON: {error}"
        ) from error
    except OSError as error:
        raise StorageError(
            f"Unable to read courses.json: {error}"
        ) from error

    # The file should contain a JSON array of courses.
    if not isinstance(courses, list):
        raise StorageError("courses.json must contain a JSON array.")

    return courses


def save_courses(courses):
    """
    Save the complete course list to courses.json.

    The file is only replaced after the new contents have been written
    successfully to a temporary file.
    """
    temporary_file = COURSES_FILE.with_suffix(".json.tmp")

    try:
        with temporary_file.open("w", encoding="utf-8") as file:
            json.dump(courses, file, indent=2)
            file.write("\n")

        temporary_file.replace(COURSES_FILE)
    except OSError as error:
        # Clean up the temporary file if saving failed.
        try:
            if temporary_file.exists():
                temporary_file.unlink()
        except OSError:
            pass

        raise StorageError(
            f"Unable to write courses.json: {error}"
        ) from error


def storage_error_response(error):
    """Return the standard response for file or JSON storage errors."""
    return jsonify({
        "success": False,
        "error": str(error),
    }), 500


def get_json_object():
    """
    Parse the request body and require it to be a JSON object.

    This handles malformed JSON, empty bodies, arrays, strings, and
    other non-object JSON values with a clear 400 response.
    """
    if not request.data:
        return None, (jsonify({
            "success": False,
            "error": "Request body must contain valid JSON.",
        }), 400)

    try:
        data = request.get_json()
    except Exception:
        return None, (jsonify({
            "success": False,
            "error": "Request body contains malformed JSON.",
        }), 400)

    if not isinstance(data, dict):
        return None, (jsonify({
            "success": False,
            "error": "Request body must be a JSON object.",
        }), 400)

    return data, None


def validate_date(value):
    """Return an error message if value is not a real YYYY-MM-DD date."""
    if not isinstance(value, str) or not value.strip():
        return "target_date must be a non-empty string."

    if not DATE_PATTERN.fullmatch(value):
        return "target_date must use the YYYY-MM-DD format."

    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return "target_date must be a real calendar date."

    return None


def validate_course_fields(data, require_all_fields=False):
    """
    Validate course fields.

    For POST, all fields are required. For PUT, only fields included in
    the request are validated.
    """
    required_fields = {
        "name",
        "description",
        "target_date",
        "status",
    }

    if require_all_fields:
        missing_fields = [
            field for field in required_fields
            if field not in data
        ]

        if missing_fields:
            return (
                "Missing required field(s): "
                + ", ".join(sorted(missing_fields))
                + "."
            )

    allowed_fields = required_fields
    unknown_fields = sorted(set(data) - allowed_fields)

    if unknown_fields:
        return (
            "Unknown field(s): "
            + ", ".join(unknown_fields)
            + "."
        )

    for field in ("name", "description"):
        if field in data:
            if not isinstance(data[field], str) or not data[field].strip():
                return f"{field} must be a non-empty string."

    if "target_date" in data:
        date_error = validate_date(data["target_date"])
        if date_error:
            return date_error

    if "status" in data:
        if (
            not isinstance(data["status"], str)
            or data["status"] not in ALLOWED_STATUSES
        ):
            return (
                "status must be one of: "
                "Not Started, In Progress, Completed."
            )

    return None


def find_course(courses, course_id):
    """Find a course by integer ID."""
    for course in courses:
        if isinstance(course, dict) and course.get("id") == course_id:
            return course

    return None


@app.route("/api/courses", methods=["GET"])
def list_courses():
    """Return all courses."""
    try:
        courses = read_courses()
    except StorageError as error:
        return storage_error_response(error)

    return jsonify({
        "success": True,
        "count": len(courses),
        "courses": courses,
    }), 200


@app.route("/api/courses", methods=["POST"])
def create_course():
    """Validate and create a new course."""
    data, error_response = get_json_object()

    if error_response:
        return error_response

    validation_error = validate_course_fields(
        data,
        require_all_fields=True,
    )

    if validation_error:
        return jsonify({
            "success": False,
            "error": validation_error,
        }), 400

    try:
        courses = read_courses()

        # Generate the next integer ID, starting at 1.
        existing_ids = [
            course.get("id")
            for course in courses
            if isinstance(course, dict)
            and isinstance(course.get("id"), int)
            and not isinstance(course.get("id"), bool)
        ]
        next_id = max(existing_ids, default=0) + 1

        # Generate a UTC timestamp on the server.
        created_at = (
            datetime.now()
            .astimezone()
            .isoformat(timespec="seconds")
        )

        course = {
            "id": next_id,
            "name": data["name"],
            "description": data["description"],
            "target_date": data["target_date"],
            "status": data["status"],
            "created_at": created_at,
        }

        courses.append(course)
        save_courses(courses)
    except StorageError as error:
        return storage_error_response(error)

    return jsonify({
        "success": True,
        "message": "Course created successfully.",
        "course": course,
    }), 201


@app.route("/api/courses/<int:course_id>", methods=["GET"])
def get_course(course_id):
    """Return one course by ID."""
    try:
        courses = read_courses()
    except StorageError as error:
        return storage_error_response(error)

    course = find_course(courses, course_id)

    if course is None:
        return jsonify({
            "success": False,
            "error": "Course not found.",
        }), 404

    return jsonify({
        "success": True,
        "course": course,
    }), 200


@app.route("/api/courses/<int:course_id>", methods=["PUT"])
def update_course(course_id):
    """Update only the fields supplied in the request body."""
    data, error_response = get_json_object()

    if error_response:
        return error_response

    validation_error = validate_course_fields(data)

    if validation_error:
        return jsonify({
            "success": False,
            "error": validation_error,
        }), 400

    try:
        courses = read_courses()
    except StorageError as error:
        return storage_error_response(error)

    course = find_course(courses, course_id)

    if course is None:
        return jsonify({
            "success": False,
            "error": "Course not found.",
        }), 404

    # Only update supplied fields. ID and created_at are preserved.
    for field in ("name", "description", "target_date", "status"):
        if field in data:
            course[field] = data[field]

    try:
        save_courses(courses)
    except StorageError as error:
        return storage_error_response(error)

    return jsonify({
        "success": True,
        "message": "Course updated successfully.",
        "course": course,
    }), 200


@app.route("/api/courses/<int:course_id>", methods=["DELETE"])
def delete_course(course_id):
    """Delete one course by ID."""
    try:
        courses = read_courses()
    except StorageError as error:
        return storage_error_response(error)

    course = find_course(courses, course_id)

    if course is None:
        return jsonify({
            "success": False,
            "error": "Course not found.",
        }), 404

    courses.remove(course)

    try:
        save_courses(courses)
    except StorageError as error:
        return storage_error_response(error)

    return jsonify({
        "success": True,
        "message": "Course deleted successfully.",
        "deleted_course": course,
    }), 200


if __name__ == "__main__":
    # Debug mode is intentionally disabled.
    app.run(host="0.0.0.0", port=5000, debug=False)