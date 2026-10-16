
import os
import subprocess
import shutil
import csv
import json
import uuid
import tempfile
from pathlib import Path
from io import BytesIO, StringIO

from flask import Flask, render_template, request, send_file, jsonify
from PIL import Image, ImageOps
import pandas as pd

app = Flask(__name__)

app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB

ALLOWED_EXTENSIONS = {
    "pdf", "docx", "txt", "png", "jpg", "jpeg",
    "webp", "xlsx", "csv", "json"
}

TEMP_DIR = Path(tempfile.gettempdir()) / "fileflow"
TEMP_DIR.mkdir(parents=True, exist_ok=True)
LIBREOFFICE_PATH = (
    os.environ.get("LIBREOFFICE_PATH")
    or (
        r"C:\Program Files\LibreOffice\program\soffice.com"
        if os.name == "nt"
        else shutil.which("libreoffice") or shutil.which("soffice")
    )
)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/convert", methods=["POST"])
def convert():
    uploaded = request.files.get("file")
    target = request.form.get("target", "").lower().strip()

    if not uploaded or not uploaded.filename:
        return jsonify(error="Please choose a file."), 400

    original_name = Path(uploaded.filename).name
    source = Path(original_name).suffix.lower().lstrip(".")

    if source not in ALLOWED_EXTENSIONS:
        return jsonify(error="This source file type is not supported."), 400

    allowed_targets = {
        "pdf", "png", "jpg", "webp", "csv", "xlsx", "json"
    }
    if target not in allowed_targets:
        return jsonify(error="Please select a supported output format."), 400

    job_dir = TEMP_DIR / uuid.uuid4().hex
    job_dir.mkdir(parents=True)
    input_path = job_dir / f"input.{source}"
    output_path = job_dir / f"converted.{target}"

    
    try:
        uploaded.save(input_path)

        # Word and text documents to PDF
        if source in {"docx", "txt"} and target == "pdf":
            output_dir = job_dir / "pdf_output"
            output_dir.mkdir()

            if source == "txt":
                import html

                text_content = input_path.read_text(encoding="utf-8")
                convert_path = job_dir / "document.html"
                convert_path.write_text(
                    "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                    "<style>body{font-family:Arial,sans-serif;"
                    "white-space:pre-wrap;overflow-wrap:anywhere;}</style>"
                    "</head><body>" + html.escape(text_content) +
                    "</body></html>",
                    encoding="utf-8"
                )
            else:
                convert_path = input_path

            profile_dir = job_dir / "lo_profile"
            profile_dir.mkdir()

            command = [
                LIBREOFFICE_PATH,
                "-env:UserInstallation=" + profile_dir.resolve().as_uri(),
                "--headless",
                "--convert-to", "pdf",
                "--outdir", str(output_dir.resolve()),
                str(convert_path.resolve())
            ]

            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=60,
                check=False
            )

            generated_pdf = output_dir / (convert_path.stem + ".pdf")

            if result.returncode != 0 or not generated_pdf.exists():
                raise ValueError(
                    "PDF conversion failed. Please check your document."
                )

            shutil.copy2(generated_pdf, output_path)

        # Image conversions
        elif source in {"png", "jpg", "jpeg", "webp"} and target in {
            "png", "jpg", "webp"
        }:
            with Image.open(input_path) as image:
                image = ImageOps.exif_transpose(image)

                if target == "jpg":
                    image = image.convert("RGB")
                    output_path = job_dir / "converted.jpg"
                    image.save(output_path, "JPEG", quality=90)
                elif target == "png":
                    image.save(output_path, "PNG")
                else:
                    image.save(output_path, "WEBP", quality=90)

        # Spreadsheet and structured-data conversions
        elif source in {"csv", "xlsx", "json"} and target in {
            "csv", "xlsx", "json"
        }:
            if source == "csv":
                data = pd.read_csv(input_path)
            elif source == "xlsx":
                data = pd.read_excel(input_path)
            else:
                with open(input_path, "r", encoding="utf-8") as file:
                    records = json.load(file)

                if not isinstance(records, list) or not all(
                    isinstance(item, dict) for item in records
                ):
                    raise ValueError(
                        "JSON-to-table conversion requires a JSON array of objects."
                    )

                data = pd.DataFrame(records)

            if target == "csv":
                data.to_csv(output_path, index=False)
            elif target == "xlsx":
                data.to_excel(output_path, index=False)
            else:
                data.to_json(output_path, orient="records", indent=2)

        else:
            raise ValueError(
                "This conversion is not implemented yet. "
                "Choose an available conversion, such as an image or spreadsheet."
            )


        download_name = f"{Path(original_name).stem}.{target}"

        response = send_file(
            output_path,
            as_attachment=True,
            download_name=download_name
        )

        response.call_on_close(lambda: __import__("shutil").rmtree(
            job_dir, ignore_errors=True
        ))
        return response

    except Exception as error:
        print(f"FileFlow conversion error: {error!r}")
        shutil.rmtree(job_dir, ignore_errors=True)
        message = str(error) if isinstance(error, ValueError) else (
            "Conversion failed. Please check your file and try again."
        )
        return jsonify(error=message), 400


@app.errorhandler(413)
def too_large(_error):
    return jsonify(error="File too large. Maximum size is 20 MB."), 413


if __name__ == "__main__":
    app.run(debug=True)
