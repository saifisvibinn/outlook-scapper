"""Generate icon.ico from icon.png for PyInstaller."""
from pathlib import Path

from PIL import Image

root = Path(__file__).resolve().parent
png = root / "icon.png"
ico = root / "icon.ico"

if not png.exists():
    raise SystemExit(f"icon.png not found at {png}")

img = Image.open(png)
img.save(
    ico,
    sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
)
print(f"Created {ico}")
