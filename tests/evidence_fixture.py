import io
from PIL import Image


def picture_evidence():
    data = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(data, format="PNG")
    return "evidence.png", "image", data.getvalue()
